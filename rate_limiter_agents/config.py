from __future__ import annotations

import os

from .providers.base import LLMProviderType

AGENT_DB_URL: str = os.getenv("AGENT_DB_URL", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
AGENT_INTERVAL_MINUTES: int = int(os.getenv("AGENT_INTERVAL_MINUTES", "15"))
CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "http://localhost:8000").split(",")

LLM_PROVIDER: LLMProviderType = LLMProviderType(os.getenv("LLM_PROVIDER", "anthropic"))
LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")

# MCP server (rate-limiting-service). Required — all rate-limit data comes from MCP.
MCP_SERVER_URL: str = os.getenv("MCP_SERVER_URL", "")
MCP_SECRET: str = os.getenv(
    "MCP_SECRET", "local-dev-mcp-secret-do-not-use-in-production"
)
