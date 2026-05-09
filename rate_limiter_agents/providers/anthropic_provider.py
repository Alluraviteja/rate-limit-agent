from __future__ import annotations

import anthropic

from .base import BaseLLMProvider, LLMResponse

# Haiku pricing: $1/M input tokens, $5/M output tokens
_INPUT_COST_PER_TOKEN = 0.000001
_OUTPUT_COST_PER_TOKEN = 0.000005


class AnthropicProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def complete(self, system: str, user: str, max_tokens: int) -> LLMResponse:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        inp = response.usage.input_tokens
        out = response.usage.output_tokens
        text = next(
            b.text for b in response.content if isinstance(b, anthropic.types.TextBlock)
        )
        return LLMResponse(
            content=text,
            input_tokens=inp,
            output_tokens=out,
            cost_usd=inp * _INPUT_COST_PER_TOKEN + out * _OUTPUT_COST_PER_TOKEN,
        )
