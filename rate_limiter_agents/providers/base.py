from __future__ import annotations

import logging
import random
import threading
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
        "TimeoutError",  # per-call execution timeout
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
    _call_timeout: float = 30.0  # seconds before a hung provider call is abandoned

    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int) -> LLMResponse:
        """Send a system+user prompt and return a normalized response."""

    def _complete_with_timeout(
        self, system: str, user: str, max_tokens: int
    ) -> LLMResponse:
        """Run complete() on a daemon thread; raise TimeoutError if it stalls."""
        result: list[LLMResponse | None] = [None]
        exc: list[BaseException | None] = [None]

        def _run() -> None:
            try:
                result[0] = self.complete(system, user, max_tokens)
            except BaseException as e:
                exc[0] = e

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=self._call_timeout)
        if t.is_alive():
            raise TimeoutError(f"LLM call timed out after {self._call_timeout}s")
        if exc[0] is not None:
            raise exc[0]
        return result[0]  # type: ignore[return-value]

    def complete_with_retry(
        self, system: str, user: str, max_tokens: int
    ) -> LLMResponse:
        """complete() with per-call timeout and exponential-backoff retry."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                return self._complete_with_timeout(system, user, max_tokens)
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
