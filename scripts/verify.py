#!/usr/bin/env python3
"""Check a running backend against the API contract: health, chat, cache, history, validation, failover.

    python3 scripts/verify.py                      # http://localhost:8000
    python3 scripts/verify.py https://my-backend   # a deployed backend

Standard library only. Makes 5 live model calls. Exits 1 if any check fails.
"""

import json
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
RUN = uuid.uuid4().hex[:6]  # makes every message unique, so an earlier run cannot have cached it
results = []


def call(method, path, body=None, headers=None, raw=None):
    """Return (status, parsed JSON or None)."""
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            status, text = response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        status, text = error.code, error.read().decode()
    try:
        return status, json.loads(text)
    except ValueError:
        return status, None


def chat(session_id, message, simulate=None):
    headers = {"X-Simulate-Failure": simulate} if simulate else None
    return call("POST", "/chat", {"session_id": session_id, "message": message}, headers)


def check(name, passed, detail=""):
    results.append(passed)
    print(f"  {'PASS' if passed else 'FAIL'}  {name}" + (f"\n        {detail}" if detail and not passed else ""))


def new_session(label):
    return f"verify-{RUN}-{label}"


print(f"Checking {BASE}\n")

# --- health (a free host may need up to a minute to wake up) ---
status, health = 0, None
for attempt in range(30):
    try:
        status, health = call("GET", "/health")
        if status == 200:
            break
    except OSError:
        pass
    if attempt == 0:
        print("  waiting for the backend to answer ...")
    time.sleep(4)
check("GET /health is 200 with status ok", status == 200 and (health or {}).get("status") == "ok", f"{status} {health}")
if status != 200:
    sys.exit("\nThe backend is not reachable, so the other checks were skipped.")
check("at least 2 keys configured, cache enabled",
      health.get("keys_configured", 0) >= 2 and health.get("cache_enabled") is True, str(health))

# --- live call ---
question = f"What is the capital of France? (run {RUN})"
session_a = new_session("a")
status, live = chat(session_a, question)
if status != 200:
    check("POST /chat answers 200", False, f"{status} {live}")
    sys.exit(
        "\nNo key could answer. Check LLM_API_KEYS and LLM_MODEL, then read the backend log:\n"
        "it shows the provider's own error (wrong key, unknown model, quota)."
    )
meta = live["metadata"]
check("chat returns session_id, a non-empty reply and metadata",
      live.get("session_id") == session_a and isinstance(live.get("reply"), str) and live["reply"].strip() != "")
check("first call is LIVE_INFERENCE and names the key that answered",
      meta.get("resolution_source") == "LIVE_INFERENCE" and str(meta.get("key_id", "")).startswith("key_"), str(meta))
check("model is a string; latency and tokens are positive integers",
      isinstance(meta.get("model"), str)
      and all(type(meta.get(field)) is int and meta[field] > 0 for field in ("latency_ms", "input_tokens", "output_tokens"))
      and type(meta.get("fallback_attempts")) is int, str(meta))

# --- cache: the same first message in a new session ---
session_b = new_session("b")
status, cached = chat(session_b, question)
cached_meta = (cached or {}).get("metadata", {})
check("same first message in a new session is LLM_CACHE", cached_meta.get("resolution_source") == "LLM_CACHE", str(cached_meta))
check("cache hit returns the identical reply", (cached or {}).get("reply") == live["reply"])
check("cache hit has key_id null and fallback_attempts 0",
      cached_meta.get("key_id", "x") is None and cached_meta.get("fallback_attempts") == 0, str(cached_meta))
check("cache hit reports the tokens stored with the cached response",
      cached_meta.get("input_tokens") == meta["input_tokens"] and cached_meta.get("output_tokens") == meta["output_tokens"])
check("cache hit is faster than the live call",
      cached_meta.get("latency_ms", 10**9) < meta["latency_ms"], f"cache {cached_meta.get('latency_ms')} ms vs live {meta['latency_ms']} ms")

# --- multi-turn memory ---
status, _ = chat(session_a, "My favourite number is 7391. Please remember it.")
status, recall = chat(session_a, "What is my favourite number? Answer with the number only.")
recall_meta = (recall or {}).get("metadata", {})
check("the bot remembers earlier messages in the session", "7391" in (recall or {}).get("reply", ""), str(recall))
check("a different message is never served from cache", recall_meta.get("resolution_source") == "LIVE_INFERENCE")
check("tokens are per request: a later turn sends more input than the first",
      recall_meta.get("input_tokens", 0) > meta["input_tokens"],
      f"turn 3 input {recall_meta.get('input_tokens')} vs turn 1 input {meta['input_tokens']}")

# --- history endpoint ---
status, history = call("GET", f"/sessions/{session_a}/messages")
messages = (history or {}).get("messages", [])
check("GET /sessions/{id}/messages returns the 6 stored messages, oldest first",
      status == 200 and [m.get("role") for m in messages] == ["user", "assistant"] * 3
      and messages[0].get("content") == question, f"{status} {history}")
check("assistant messages carry metadata, user messages do not, no system prompt is stored",
      len(messages) == 6
      and all("metadata" not in m for m in messages[0::2])
      and all({"resolution_source", "input_tokens", "output_tokens"} <= set(m.get("metadata", {})) for m in messages[1::2]))
status, other = call("GET", f"/sessions/{session_b}/messages")
check("sessions stay separate", status == 200 and len((other or {}).get("messages", [])) == 2)
status, body = call("GET", f"/sessions/{new_session('unknown')}/messages")
check("unknown session is 404 session not found", status == 404 and body == {"detail": "session not found"}, f"{status} {body}")

# --- validation ---
session_v = new_session("v")
bad_requests = {
    "empty message": {"session_id": session_v, "message": ""},
    "whitespace-only message": {"session_id": session_v, "message": "   "},
    "message over 4000 characters": {"session_id": session_v, "message": "x" * 4001},
    "missing message": {"session_id": session_v},
    "missing session_id": {"message": "hi"},
    "empty session_id": {"session_id": "", "message": "hi"},
}
for name, payload in bad_requests.items():
    status, body = call("POST", "/chat", payload)
    check(f"422 for {name}", status == 422 and isinstance(body, dict) and "detail" in body, f"{status} {body}")
status, body = call("POST", "/chat", raw=b"{not json")
check("422 for malformed JSON", status == 422 and isinstance(body, dict) and "detail" in body, f"{status} {body}")
status, _ = call("GET", f"/sessions/{session_v}/messages")
check("rejected requests are not added to history", status == 404)

# --- failover through X-Simulate-Failure ---
same_length = f"What is the capital of Spain?  (run {RUN})"  # as long as the first question
for code in (429, 500):
    session = new_session(f"f{code}")
    status, body = chat(session, same_length if code == 429 else f"Say hello. (run {RUN}, {code})", simulate=f"key_1={code}")
    fail_meta = (body or {}).get("metadata", {})
    check(f"key_1={code} fails over to key_2 with a real answer",
          status == 200 and fail_meta.get("key_id") == "key_2" and fail_meta.get("fallback_attempts") == 1
          and fail_meta.get("resolution_source") == "LIVE_INFERENCE" and (body or {}).get("reply", "").strip() != "",
          f"{status} {body}")
    if code == 429:
        check("two equal-length first messages report about the same input tokens",
              abs(fail_meta.get("input_tokens", 0) - meta["input_tokens"]) <= 3,
              f"{fail_meta.get('input_tokens')} vs {meta['input_tokens']}")
status, body = chat(session_a, "This must not be stored.", simulate="*=429")
check("*=429 is 503 all providers unavailable", status == 503 and body == {"detail": "all providers unavailable"}, f"{status} {body}")
status, history = call("GET", f"/sessions/{session_a}/messages")
check("a 503 adds nothing to history", len((history or {}).get("messages", [])) == 6)

passed = sum(results)
print(f"\n{passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
