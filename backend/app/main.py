"""FastAPI app with exactly three endpoints: GET /health, POST /chat, GET /sessions/{session_id}/messages."""

import logging
import time

import litellm
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .config import Settings
from .db import Database
from .llm import LLMGateway, parse_simulation

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer clearly and concisely, in plain text without Markdown formatting."
)

log = logging.getLogger("uvicorn.error")


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128, pattern=r"\S")  # \S: not only whitespace
    message: str
    system_prompt: str | None = Field(default=None, max_length=4000)

    @field_validator("message")
    @classmethod
    def trim_message(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 4000:
            raise ValueError("message must be 1 to 4000 characters after trimming whitespace")
        return value


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.database_url)
    gateway = LLMGateway(settings.api_keys, settings.model, settings.api_base)

    # The contract is exactly three endpoints, so the generated docs routes are switched off.
    app = FastAPI(title="LLM chat gateway", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],  # includes X-Simulate-Failure
    )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Last resort: the client always gets JSON with a detail field, never a stack trace.
        log.exception("unexpected error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    @app.get("/health")
    def health() -> dict:
        try:
            db.ping()
        except Exception:
            raise HTTPException(503, "database unavailable")
        return {"status": "ok", "keys_configured": len(gateway.key_ids), "cache_enabled": litellm.cache is not None}

    @app.post("/chat")
    async def chat(body: ChatRequest, x_simulate_failure: str | None = Header(default=None)) -> dict:
        started = time.perf_counter()

        simulate_failure = None
        if settings.simulation_enabled and x_simulate_failure:
            try:
                simulate_failure = parse_simulation(x_simulate_failure, gateway.key_ids)
            except ValueError as exc:
                raise HTTPException(422, str(exc))

        session = await run_in_threadpool(db.get_session, body.session_id)  # None: a new session
        history = session["messages"] if session else []
        if body.system_prompt is not None:
            # Sent with this request: it becomes the session's prompt. Blank goes back to the default.
            system_prompt = body.system_prompt.strip() or None
        else:
            system_prompt = session["system_prompt"] if session else None

        messages = [
            {"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT},
            *({"role": message["role"], "content": message["content"]} for message in history),
            {"role": "user", "content": body.message},
        ]
        try:
            result = await gateway.complete(messages, simulate_failure)
        except Exception as exc:
            # LiteLLM has already tried every key in order. Nothing was saved, so history is unchanged.
            log.warning("all keys failed for this request: %s", type(exc).__name__)
            raise HTTPException(503, "all providers unavailable")

        metadata = {
            "resolution_source": result["resolution_source"],
            "model": result["model"],
            "latency_ms": max(1, round((time.perf_counter() - started) * 1000)),
            "input_tokens": result["input_tokens"],
            "output_tokens": result["output_tokens"],
            "key_id": result["key_id"],
            "fallback_attempts": result["fallback_attempts"],
        }
        await run_in_threadpool(
            db.save_exchange, body.session_id, system_prompt, body.message, result["reply"], metadata
        )
        return {"session_id": body.session_id, "reply": result["reply"], "metadata": metadata}

    @app.get("/sessions/{session_id}/messages")
    def session_messages(session_id: str) -> dict:
        session = db.get_session(session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        return {"session_id": session_id, "messages": session["messages"]}

    return app
