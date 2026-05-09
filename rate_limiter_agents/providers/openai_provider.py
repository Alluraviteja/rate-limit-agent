from __future__ import annotations

from .base import BaseLLMProvider, LLMResponse

# gpt-4o-mini pricing: $0.15/M input tokens, $0.60/M output tokens
_INPUT_COST_PER_TOKEN = 0.00000015
_OUTPUT_COST_PER_TOKEN = 0.0000006


class OpenAIProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise ImportError("openai package required: pip install openai") from e
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def complete(self, system: str, user: str, max_tokens: int) -> LLMResponse:
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        inp = response.usage.prompt_tokens
        out = response.usage.completion_tokens
        return LLMResponse(
            content=response.choices[0].message.content,
            input_tokens=inp,
            output_tokens=out,
            cost_usd=inp * _INPUT_COST_PER_TOKEN + out * _OUTPUT_COST_PER_TOKEN,
        )
