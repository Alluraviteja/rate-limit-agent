from __future__ import annotations

from .. import config
from .base import BaseLLMProvider, LLMProviderType


def get_provider() -> BaseLLMProvider:
    """Return the configured LLM provider instance."""
    if config.LLM_PROVIDER is LLMProviderType.ANTHROPIC:
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=config.ANTHROPIC_API_KEY, model=config.LLM_MODEL
        )

    if config.LLM_PROVIDER is LLMProviderType.OPENAI:
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(api_key=config.OPENAI_API_KEY, model=config.LLM_MODEL)

    raise ValueError(f"Unhandled LLMProviderType: {config.LLM_PROVIDER}")
