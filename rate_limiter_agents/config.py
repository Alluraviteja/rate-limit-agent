from __future__ import annotations

import os

from .providers.base import LLMProviderType

RATE_LIMITER_DB: str = os.getenv("RATE_LIMITER_DB", "")
AGENT_DB_URL: str = os.getenv("AGENT_DB_URL", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
AGENT_INTERVAL_MINUTES: int = int(os.getenv("AGENT_INTERVAL_MINUTES", "15"))
CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "http://localhost:8000").split(",")

LLM_PROVIDER: LLMProviderType = LLMProviderType(os.getenv("LLM_PROVIDER", "anthropic"))
LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")
