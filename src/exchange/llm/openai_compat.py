"""OpenAI-compatible provider — covers both Ollama and DeepSeek.

Ollama serves an OpenAI-compatible endpoint at /v1, and DeepSeek's API is
OpenAI-compatible too, so the same client class reaches both. They differ
only in base_url, key and model name.
"""
from __future__ import annotations

import logging
import os

from openai import BadRequestError, OpenAI

from exchange.llm.base import LLMMessage, LLMResponse

_log = logging.getLogger(__name__)

OLLAMA_BASE_URL = "http://localhost:11434/v1"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"
# llama3.2 rather than a reasoning model: qwen3 spends ~700 tokens thinking
# before emitting anything, so under a small max_tokens it returns an empty
# string having consumed the whole budget. Negotiation parses a structured
# reply under a 256-token cap and needs an immediate answer.
DEFAULT_OLLAMA_MODEL = "llama3.2:latest"


class OpenAICompatProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 120.0,
    ) -> None:
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self._model = model

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        payload = []
        if system:
            payload.append({"role": "system", "content": system})
        payload.extend({"role": m.role, "content": m.content} for m in messages)

        extra: dict = {}
        if reasoning_effort:
            extra["reasoning_effort"] = reasoning_effort

        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=payload,
                max_tokens=max_tokens,
                **extra,
            )
        except BadRequestError as exc:
            # Some local models (e.g. llama3.2 via Ollama) reject the
            # reasoning_effort param outright rather than ignoring it —
            # retry once without it rather than failing the whole call.
            if extra and "thinking" in str(exc).lower():
                # Say so. A run where reasoning was silently disabled produces
                # different agent behaviour from one where it was not, and
                # without this line the two are indistinguishable afterwards.
                _log.warning(
                    "model %r rejected reasoning_effort=%r; retrying without it",
                    self._model, reasoning_effort,
                )
                completion = self._client.chat.completions.create(
                    model=self._model,
                    messages=payload,
                    max_tokens=max_tokens,
                )
            else:
                raise
        choice = completion.choices[0]

        # AN EMPTY REPLY IS A BUDGET FAILURE, NOT AN ANSWER — and on this
        # family of models it is the default failure. deepseek-v4-pro spends
        # its token budget reasoning before it emits a character, so a budget
        # that only covers the answer returns `""` with finish_reason
        # "length" and full usage billed. Measured: max_tokens=800 with the
        # default effort produced 800 reasoning tokens and no text, every
        # time, on the real negotiation prompt.
        #
        # Retried once with room to finish rather than returned blank,
        # because downstream an empty string is not an error — it is an
        # unparseable offer, an absent bid, a headline of "". Every one of
        # those is a silent corruption of a paid run, and each is far more
        # expensive than the retry.
        text = choice.message.content or ""
        if not text.strip() and choice.finish_reason == "length":
            _log.warning(
                "%r returned no text within %d tokens (finish_reason=length); "
                "retrying once with %d",
                self._model, max_tokens, max_tokens * 3,
            )
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=payload,
                max_tokens=max_tokens * 3,
                **extra,
            )
            choice = completion.choices[0]
            text = choice.message.content or ""

        usage = completion.usage
        return LLMResponse(
            text=text,
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            model=self._model,
        )


def provider_from_env() -> OpenAICompatProvider:
    """Build a provider from LLM_PROVIDER / LLM_MODEL / DEEPSEEK_API_KEY.

    Callers must have loaded .env themselves — this reads the environment
    only, for the same reason Config.from_env does.
    """
    which = os.environ.get("LLM_PROVIDER", "ollama").lower()

    if which == "ollama":
        return OpenAICompatProvider(
            base_url=OLLAMA_BASE_URL,
            api_key="ollama",  # Ollama ignores it, the SDK requires one
            model=os.environ.get("LLM_MODEL", DEFAULT_OLLAMA_MODEL),
        )

    if which == "deepseek":
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            raise ValueError("LLM_PROVIDER=deepseek but DEEPSEEK_API_KEY is not set")
        return OpenAICompatProvider(
            base_url=DEEPSEEK_BASE_URL,
            api_key=key,
            model=os.environ.get("LLM_MODEL", DEFAULT_DEEPSEEK_MODEL),
        )

    raise ValueError(f"Unknown LLM_PROVIDER {which!r}; expected 'ollama' or 'deepseek'")


def providers_from_env() -> tuple[OpenAICompatProvider, OpenAICompatProvider]:
    """Return (strong, fast).

    The strong tier carries judgment — consolidating an episode, choosing a
    counterparty, valuing an insight. The fast tier carries the narrow roles.
    The expensive model is the rarest call, so mixed tiering costs less than
    running one tier everywhere, not more.

    LLM_MODEL_STRONG / LLM_MODEL_FAST override per tier; both fall back to
    LLM_MODEL so local development needs no extra configuration.
    """
    base = provider_from_env()
    strong_model = os.environ.get("LLM_MODEL_STRONG")
    fast_model = os.environ.get("LLM_MODEL_FAST")
    if not strong_model and not fast_model:
        return base, base
    return (
        _retier(base, strong_model) if strong_model else base,
        _retier(base, fast_model) if fast_model else base,
    )


def _retier(base: OpenAICompatProvider, model: str) -> OpenAICompatProvider:
    """A sibling provider on the same endpoint and key, different model."""
    clone = OpenAICompatProvider.__new__(OpenAICompatProvider)
    clone._client = base._client
    clone._model = model
    return clone
