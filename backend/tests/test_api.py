"""API contract tests.

Everything is real except the model: LiteLLM (router, fallback, cache) talks over HTTP to the
fake provider in fake_provider.py, which echoes the user messages it receives.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.main import DEFAULT_SYSTEM_PROMPT, create_app
from tests.fake_provider import FakeProvider

MODEL = "gemini/gemini-3.5-flash-lite"
ALL_DOWN = {"detail": "all providers unavailable"}


@pytest.fixture(scope="session")
def provider_server():
    fake = FakeProvider()
    yield fake
    fake.close()


@pytest.fixture
def provider(provider_server):
    provider_server.requests.clear()
    return provider_server


def make_client(tmp_path, provider, keys=("test-key-1", "test-key-2"), simulation=True):
    settings = Settings(
        api_keys=list(keys),
        model=MODEL,
        database_url=f"sqlite:///{tmp_path}/test.db",
        allowed_origins=["http://localhost:3000"],
        simulation_enabled=simulation,
        api_base=provider.url,
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


@pytest.fixture
def client(tmp_path, provider):
    with make_client(tmp_path, provider) as test_client:
        yield test_client


def chat(client, session_id, message, simulate=None, **fields):
    headers = {"X-Simulate-Failure": simulate} if simulate else {}
    return client.post("/chat", json={"session_id": session_id, "message": message, **fields}, headers=headers)


def history(client, session_id):
    return client.get(f"/sessions/{session_id}/messages")


# --- health ---------------------------------------------------------------------------------------


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


# --- chat, cache and tokens -----------------------------------------------------------------------


def test_chat_returns_the_contract_fields(client):
    response = chat(client, "s-1", "  What is the capital of France?  ")
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "s-1"
    assert body["reply"] == "You said: What is the capital of France?"  # the message was trimmed
    metadata = body["metadata"]
    assert metadata["resolution_source"] == "LIVE_INFERENCE"
    assert metadata["model"] == MODEL
    assert metadata["key_id"] == "key_1"
    assert metadata["fallback_attempts"] == 0
    for field in ("latency_ms", "input_tokens", "output_tokens"):
        assert type(metadata[field]) is int and metadata[field] > 0


def test_same_first_message_in_a_new_session_comes_from_cache(client, provider):
    live = chat(client, "s-1", "What is the capital of France?").json()
    cached = chat(client, "s-2", "What is the capital of France?").json()
    assert cached["metadata"]["resolution_source"] == "LLM_CACHE"
    assert cached["reply"] == live["reply"]
    assert cached["metadata"]["key_id"] is None
    assert cached["metadata"]["fallback_attempts"] == 0
    # the tokens are the usage stored with the cached response
    assert cached["metadata"]["input_tokens"] == live["metadata"]["input_tokens"]
    assert cached["metadata"]["output_tokens"] == live["metadata"]["output_tokens"]
    assert len(provider.requests) == 1  # the second request never reached the model


def test_a_different_message_never_comes_from_cache(client):
    chat(client, "s-1", "What is the capital of France?")
    other = chat(client, "s-2", "What is the capital of Spain?").json()
    assert other["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert other["reply"] == "You said: What is the capital of Spain?"


def test_cache_key_covers_the_whole_message_list(client):
    chat(client, "s-1", "Hello")
    # the same text again in the same session has a longer history, so it is a new cache key
    assert chat(client, "s-1", "Hello").json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    # a different system prompt is a different message list too
    custom = chat(client, "s-2", "Hello", system_prompt="Answer in French.").json()
    assert custom["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_tokens_are_per_request_not_a_running_total(client):
    first = chat(client, "s-1", "aaaa bbbb cccc").json()["metadata"]
    second = chat(client, "s-1", "dddd").json()["metadata"]
    other_session = chat(client, "s-2", "eeee ffff gggg").json()["metadata"]
    assert second["input_tokens"] > first["input_tokens"]  # the prompt now also holds the first turn
    assert other_session["input_tokens"] == first["input_tokens"]  # equal-length first messages


# --- multi-turn state -----------------------------------------------------------------------------


def test_the_model_gets_the_session_history_and_sessions_stay_separate(client, provider):
    chat(client, "s-1", "My name is Asha.")
    chat(client, "s-2", "Unrelated question")
    answer = chat(client, "s-1", "What is my name?").json()
    assert "My name is Asha." in answer["reply"]
    assert "Unrelated" not in answer["reply"]
    assert provider.requests[-1]["system_prompt"] == DEFAULT_SYSTEM_PROMPT
    assert provider.requests[-1]["messages"] == [
        ("user", "My name is Asha."),
        ("model", "You said: My name is Asha."),
        ("user", "What is my name?"),
    ]


def test_history_endpoint_returns_stored_messages_oldest_first(client):
    first = chat(client, "s-1", "one").json()
    chat(client, "s-1", "two")
    response = history(client, "s-1")
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "s-1"
    assert [(m["role"], m["content"]) for m in body["messages"]] == [
        ("user", "one"),
        ("assistant", "You said: one"),
        ("user", "two"),
        ("assistant", "You said: one | two"),
    ]
    assert "metadata" not in body["messages"][0]
    assert body["messages"][1]["metadata"] == first["metadata"]


def test_history_survives_a_restart(tmp_path, provider):
    with make_client(tmp_path, provider) as before:
        chat(before, "s-1", "remember this")
    with make_client(tmp_path, provider) as after:  # a new app on the same database file
        assert len(history(after, "s-1").json()["messages"]) == 2


def test_unknown_session_is_404(client):
    response = history(client, "nope")
    assert response.status_code == 404
    assert response.json() == {"detail": "session not found"}


def test_system_prompt_overrides_the_default_for_the_session(client, provider):
    chat(client, "s-1", "Hi", system_prompt="You are a pirate.")
    chat(client, "s-1", "Again")  # not sent this time: the session keeps its prompt
    chat(client, "s-1", "Reset", system_prompt="  ")  # blank: back to the default
    assert [request["system_prompt"] for request in provider.requests] == [
        "You are a pirate.",
        "You are a pirate.",
        DEFAULT_SYSTEM_PROMPT,
    ]
    # the system prompt is never part of the stored history
    assert all(m["role"] in ("user", "assistant") for m in history(client, "s-1").json()["messages"])


# --- validation -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "s-1"},
        {"message": "hi"},
        {"session_id": "s-1", "message": ""},
        {"session_id": "s-1", "message": "   \n "},
        {"session_id": "s-1", "message": "x" * 4001},
        {"session_id": "", "message": "hi"},
        {"session_id": "  ", "message": "hi"},
        {"session_id": "s" * 129, "message": "hi"},
        {"session_id": "s-1", "message": 42},
    ],
)
def test_invalid_requests_are_422_and_not_stored(client, provider, payload):
    response = client.post("/chat", json=payload)
    assert response.status_code == 422
    assert "detail" in response.json()
    assert history(client, "s-1").status_code == 404
    assert provider.requests == []


def test_malformed_json_is_422(client):
    response = client.post("/chat", content="{not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert "detail" in response.json()


def test_length_limits_are_inclusive_and_apply_after_trimming(client):
    assert chat(client, "s" * 128, "  " + "x" * 4000 + "  ").status_code == 200


# --- failover when the provider really answers with an error status -------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_provider_error_on_key_1_falls_over_to_key_2(tmp_path, provider, status):
    with make_client(tmp_path, provider, keys=(f"bad-key-{status}", "good-key")) as client:
        body = chat(client, "s-1", "hello").json()
    assert body["reply"] == "You said: hello"
    assert body["metadata"]["key_id"] == "key_2"
    assert body["metadata"]["fallback_attempts"] == 1
    assert provider.keys_called() == [f"bad-key-{status}", "good-key"]  # key_1 was tried first, once


def test_every_key_down_is_503_and_nothing_is_stored(tmp_path, provider):
    with make_client(tmp_path, provider, keys=("bad-key-429", "bad-key-503")) as client:
        response = chat(client, "s-1", "hello")
        assert response.status_code == 503
        assert response.json() == ALL_DOWN
        assert history(client, "s-1").status_code == 404
    assert provider.keys_called() == ["bad-key-429", "bad-key-503"]


def test_the_key_id_never_exposes_the_key_itself(tmp_path, provider):
    with make_client(tmp_path, provider, keys=("bad-key-429", "good-key")) as client:
        response = chat(client, "s-1", "hello")
        stored = history(client, "s-1")
    assert "good-key" not in response.text and "bad-key" not in response.text
    assert "good-key" not in stored.text


# --- failover through the X-Simulate-Failure test hook --------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_simulated_failure_on_key_1_falls_over_to_key_2(client, provider, status):
    body = chat(client, "s-1", "hello", simulate=f"key_1={status}").json()
    assert body["reply"] == "You said: hello"  # a real answer, not an error wrapped in a 200
    assert body["metadata"]["key_id"] == "key_2"
    assert body["metadata"]["fallback_attempts"] == 1
    assert body["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert provider.keys_called() == ["test-key-2"]  # the simulated key made no real call


def test_failover_walks_the_whole_pool_in_order(tmp_path, provider):
    with make_client(tmp_path, provider, keys=("k1", "k2", "k3")) as client:
        body = chat(client, "s-1", "hello", simulate="key_1=429, key_2=503").json()
    assert body["metadata"]["key_id"] == "key_3"
    assert body["metadata"]["fallback_attempts"] == 2


@pytest.mark.parametrize("header", ["*=429", "key_1=429,key_2=500"])
def test_every_key_simulated_down_is_503_and_nothing_is_stored(client, header):
    chat(client, "s-1", "first")
    response = chat(client, "s-1", "second", simulate=header)
    assert response.status_code == 503
    assert response.json() == ALL_DOWN
    assert len(history(client, "s-1").json()["messages"]) == 2  # only the first turn
    assert chat(client, "s-new", "hi", simulate="*=500").status_code == 503
    assert history(client, "s-new").status_code == 404


def test_simulation_bypasses_the_cache_and_lasts_one_request(client):
    chat(client, "s-1", "cached question")  # now in the cache
    simulated = chat(client, "s-2", "cached question", simulate="key_1=429").json()
    assert simulated["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert simulated["metadata"]["key_id"] == "key_2"
    assert chat(client, "s-3", "cached question", simulate="*=429").status_code == 503
    # the next plain requests are untouched: the cache still answers, and key_1 is first again
    assert chat(client, "s-4", "cached question").json()["metadata"]["resolution_source"] == "LLM_CACHE"
    assert chat(client, "s-5", "new question").json()["metadata"]["key_id"] == "key_1"


def test_simulation_header_is_ignored_unless_enabled(tmp_path, provider):
    with make_client(tmp_path, provider, simulation=False) as client:
        body = chat(client, "s-1", "hello", simulate="*=429").json()
    assert body["metadata"]["key_id"] == "key_1"


@pytest.mark.parametrize("header", ["key_1", "key_1=418", "key_1=abc", "=429", "key_1=429;key_2=500"])
def test_malformed_simulation_header_is_422(client, header):
    response = chat(client, "s-1", "hello", simulate=header)
    assert response.status_code == 422
    assert "X-Simulate-Failure" in response.json()["detail"]


# --- errors and CORS ------------------------------------------------------------------------------


def test_unexpected_errors_are_json_without_a_stack_trace(client, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("disk is full")

    monkeypatch.setattr(Database, "save_exchange", broken)
    response = chat(client, "s-1", "hello")
    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error"}


def test_unknown_route_is_json_404(client):
    response = client.get("/docs")
    assert response.status_code == 404
    assert "detail" in response.json()


def test_cors_allows_the_frontend_origin_and_the_simulation_header(client):
    response = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-simulate-failure",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    blocked = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in blocked.headers
