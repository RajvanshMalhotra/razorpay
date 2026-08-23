"""The LLM interface every agent calls through.

Agents never know which provider is behind this. Local development runs
Ollama; production runs DeepSeek. Both speak the OpenAI-compatible wire
format, so one implementation covers both — but the Protocol is what agent
code depends on, so a third provider costs one new class and nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


class LLMProvider(Protocol):
    def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        ...
