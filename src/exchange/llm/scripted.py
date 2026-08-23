"""A provider that returns canned text, for tests.

Every test in this project injects one of these. No test may reach the
network — the same discipline as `fake_embedder` and `FakeRazorpay`.
"""
from __future__ import annotations

from exchange.llm.base import LLMMessage, LLMResponse


class ScriptedProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._index = 0
        self.calls: list[dict] = []

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        self.calls.append({
            "messages": messages,
            "system": system,
            "max_tokens": max_tokens,
            "reasoning_effort": reasoning_effort,
        })
        if self._index >= len(self._responses):
            raise RuntimeError(
                f"ScriptedProvider exhausted after {len(self._responses)} responses; "
                f"call {self._index + 1} has nothing to return"
            )
        text = self._responses[self._index]
        self._index += 1
        prompt_chars = sum(len(m.content) for m in messages) + len(system or "")
        return LLMResponse(
            text=text,
            input_tokens=max(1, prompt_chars // 4),
            output_tokens=max(1, len(text) // 4),
            model="scripted",
        )
