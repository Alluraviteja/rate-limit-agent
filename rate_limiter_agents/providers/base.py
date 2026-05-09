from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# Exception class names that are safe to retry across both Anthropic and OpenAI SDKs
_RETRYABLE_NAMES = frozenset(
    {
        "RateLimitError",
        "APITimeoutError",
        "InternalServerError",
        "ServiceUnavailableError",
        "APIConnectionError",
    }
)


class LLMProviderType(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class BaseLLMProvider(ABC):
    _max_retries: int = 3
    _base_delay: float = 2.0

    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int) -> LLMResponse:
        """Send a system+user prompt and return a normalized response."""

    def complete_with_retry(
        self, system: str, user: str, max_tokens: int
    ) -> LLMResponse:
        """complete() with exponential-backoff retry on transient provider errors."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                return self.complete(system, user, max_tokens)
            except Exception as exc:
                if type(exc).__name__ not in _RETRYABLE_NAMES:
                    raise
                last_exc = exc
                if attempt < self._max_retries - 1:
                    delay = self._base_delay * (2**attempt) + random.uniform(0, 1)
                    logger.warning(
                        "LLM call failed attempt %d/%d (%s), retrying in %.1fs",
                        attempt + 1,
                        self._max_retries,
                        type(exc).__name__,
                        delay,
                    )
                    time.sleep(delay)
        raise last_exc  # type: ignore[misc]
