"""All configuration comes from environment variables (see .env.example)."""

import os
from dataclasses import dataclass


def _csv(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


@dataclass
class Settings:
    api_keys: list[str]  # the key pool; key ids are key_1, key_2, ... in this order
    model: str  # LiteLLM model name, used with every key
    database_url: str
    allowed_origins: list[str]
    simulation_enabled: bool  # honour the X-Simulate-Failure header
    api_base: str | None = None  # optional provider base URL: a self-hosted or OpenAI-compatible endpoint

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            api_keys=_csv("LLM_API_KEYS"),
            model=os.getenv("LLM_MODEL", "groq/openai/gpt-oss-20b"),
            database_url=os.getenv("DATABASE_URL", "sqlite:///./app.db"),
            allowed_origins=[origin.rstrip("/") for origin in _csv("ALLOWED_ORIGINS")],
            simulation_enabled=os.getenv("ENABLE_FAILURE_SIMULATION", "").strip().lower() == "true",
            api_base=os.getenv("LLM_API_BASE") or None,
        )
