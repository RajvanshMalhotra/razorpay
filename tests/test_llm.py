import pytest

from exchange.llm.base import LLMMessage, LLMResponse
from exchange.llm.scripted import ScriptedProvider


def test_scripted_provider_returns_responses_in_order():
    provider = ScriptedProvider(["first", "second"])

    a = provider.complete([LLMMessage("user", "hi")])
    b = provider.complete([LLMMessage("user", "again")])

    assert a.text == "first"
    assert b.text == "second"


def test_scripted_provider_records_what_it_was_asked():
    provider = ScriptedProvider(["ok"])

    provider.complete([LLMMessage("user", "what is the price?")], system="You trade.")

    call = provider.calls[0]
    assert call["system"] == "You trade."
    assert call["messages"][0].content == "what is the price?"


def test_scripted_provider_raises_when_exhausted():
    """A test that makes more calls than it scripted has a bug in the test."""
    provider = ScriptedProvider(["only one"])
    provider.complete([LLMMessage("user", "hi")])

    with pytest.raises(RuntimeError, match="exhausted"):
        provider.complete([LLMMessage("user", "hi again")])


def test_scripted_provider_reports_token_counts():
    provider = ScriptedProvider(["hello there"])

    response = provider.complete([LLMMessage("user", "hi")])

    assert response.input_tokens > 0
    assert response.output_tokens > 0
    assert response.model == "scripted"


def test_deepseek_without_a_key_fails_before_any_client_is_built(monkeypatch):
    """The raise precedes client construction, so this reaches no network."""
    from exchange.llm.openai_compat import provider_from_env

    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY is not set"):
        provider_from_env()


def test_an_unknown_provider_name_is_rejected(monkeypatch):
    from exchange.llm.openai_compat import provider_from_env

    monkeypatch.setenv("LLM_PROVIDER", "anthropiq")

    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER 'anthropiq'"):
        provider_from_env()


def test_llm_message_is_frozen():
    message = LLMMessage("user", "hi")

    with pytest.raises(Exception):
        message.content = "changed"
