"""LiteLLM gateway: a key pool with automatic fallback, a response cache and the failover test hook."""

import litellm
from litellm import Router
from litellm.caching.caching import Cache
from litellm.integrations.custom_logger import CustomLogger

CACHE_TTL_SECONDS = 3600
REQUEST_TIMEOUT_SECONDS = 30  # per key; a slow key counts as a failed key

# X-Simulate-Failure status -> the error LiteLLM raises when a provider really returns that status
SIMULATED_ERRORS = {
    429: litellm.RateLimitError,
    500: litellm.InternalServerError,
    502: litellm.BadGatewayError,
    503: litellm.ServiceUnavailableError,
}


def parse_simulation(header: str, key_ids: list[str]) -> dict[str, int]:
    """Turn 'key_1=429,key_2=500' or '*=429' into {key_id: status}. Raises ValueError if malformed."""
    failures: dict[str, int] = {}
    for pair in header.split(","):
        key_id, _, status = (part.strip() for part in pair.partition("="))
        if not key_id or not status.isdigit() or int(status) not in SIMULATED_ERRORS:
            raise ValueError(
                "X-Simulate-Failure must be comma-separated <key_id>=<status> pairs, "
                "with status 429, 500, 502 or 503 and * meaning every key"
            )
        for target in key_ids if key_id == "*" else [key_id.lower()]:
            failures[target] = int(status)
    return failures


class FailureSimulator(CustomLogger):
    """Runs just before LiteLLM calls a key. A key listed for this request gets a provider error
    instead of a real call, so the failure travels through LiteLLM's own fallback path."""

    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        key_id = (kwargs.get("model_info") or {}).get("id")
        status = ((kwargs.get("metadata") or {}).get("simulate_failure") or {}).get(key_id)
        if status:
            kwargs["mock_response"] = SIMULATED_ERRORS[status](
                message=f"simulated {status}", llm_provider="simulated", model=kwargs.get("model")
            )
        return kwargs


class LLMGateway:
    def __init__(self, api_keys: list[str], model: str, api_base: str | None = None) -> None:
        if not api_keys:
            raise ValueError("LLM_API_KEYS is empty: set at least 2 comma-separated API keys")
        self.model = model
        self.key_ids = [f"key_{number}" for number in range(1, len(api_keys) + 1)]

        litellm.suppress_debug_info = True
        litellm.cache = Cache(type="local", ttl=CACHE_TTL_SECONDS)  # LiteLLM's built-in in-memory cache
        litellm.callbacks = [FailureSimulator()]

        deployments = []
        for key_id, api_key in zip(self.key_ids, api_keys):
            params = {"model": model, "api_key": api_key}
            if api_base:
                params["api_base"] = api_base
            # One model group per key, so LiteLLM's fallback list decides the order the keys are tried in.
            deployments.append({"model_name": key_id, "litellm_params": params, "model_info": {"id": key_id}})

        self.router = Router(
            model_list=deployments,
            fallbacks=[{self.key_ids[0]: self.key_ids[1:]}],  # key_1 fails -> key_2 -> key_3 ...
            max_fallbacks=len(api_keys),
            num_retries=0,  # a failed key is not retried; the next key takes over at once
            disable_cooldowns=True,  # every request starts at key_1, so one request's failure never leaks into the next
            cache_responses=True,
            caching_groups=[tuple(self.key_ids)],  # all keys share one cache entry per model + message list
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    async def complete(self, messages: list[dict], simulate_failure: dict[str, int] | None = None) -> dict:
        """Answer the message list. Raises when every key has failed."""
        options = {}
        if simulate_failure:
            options["metadata"] = {"simulate_failure": simulate_failure}
            # A simulated request is there to exercise the keys, so it neither reads nor writes the cache.
            options["cache"] = {"no-cache": True, "no-store": True}

        response = await self.router.acompletion(model=self.key_ids[0], messages=messages, **options)

        reply = (response.choices[0].message.content or "").strip()
        if not reply:
            # shortcut: an empty reply stays in the cache until its TTL ends; evict it if this shows up in practice
            raise RuntimeError("the model returned an empty reply")

        hidden = response._hidden_params  # LiteLLM's per-response details: cache hit, answering key, fallbacks
        cached = hidden.get("cache_hit") is True
        return {
            "reply": reply,
            "resolution_source": "LLM_CACHE" if cached else "LIVE_INFERENCE",
            "model": self.model,
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
            "key_id": None if cached else hidden["model_id"],
            "fallback_attempts": 0 if cached else int(hidden["additional_headers"]["x-litellm-attempted-fallbacks"]),
        }
