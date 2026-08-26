"""The provider's two retries, both for failures that are not answers.

A dropped connection and an empty reply look different and are the same
thing: the request did not produce an answer. Every caller above treats an
exception or an empty string as an OUTCOME — a turn records `error`, a
negotiation ends, a valuation becomes an absent bid — so a failure that is
really infrastructure has to be absorbed here or it becomes a market event.
"""
import pytest
from openai import APIConnectionError

from exchange.llm.base import LLMMessage
from exchange.llm.openai_compat import OpenAICompatProvider


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content, finish_reason="stop"):
        self.message = _Message(content)
        self.finish_reason = finish_reason


class _Usage:
    prompt_tokens = 10
    completion_tokens = 10


class _Completion:
    def __init__(self, content, finish_reason="stop"):
        self.choices = [_Choice(content, finish_reason)]
        self.usage = _Usage()


def _provider_with(completions):
    """A provider whose transport is the given stub."""
    provider = OpenAICompatProvider(
        base_url="http://unused", api_key="unused", model="stub",
    )

    class _Chat:
        completions = None

    provider._client = type("C", (), {"chat": type("Ch", (), {})()})()
    provider._client.chat.completions = completions
    return provider


# --- a dropped connection ----------------------------------------------------

def test_a_dropped_connection_is_retried_not_reported(monkeypatch):
    """Roughly four turns in ten died on APIConnectionError in a real run,
    while single calls from the same process succeeded every time."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    class FlakyThenFine:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise APIConnectionError(request=None)
            return _Completion("PRICE: 1900 fine")

    flaky = FlakyThenFine()

    reply = _provider_with(flaky).complete([LLMMessage(role="user", content="go")])

    assert reply.text == "PRICE: 1900 fine"
    assert flaky.calls == 3


def test_a_connection_that_never_recovers_still_raises(monkeypatch):
    """Three attempts, then the caller's own handling is right for it —
    retrying forever would hang a run rather than record a failure."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    class AlwaysDown:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            raise APIConnectionError(request=None)

    down = AlwaysDown()

    with pytest.raises(APIConnectionError):
        _provider_with(down).complete([LLMMessage(role="user", content="go")])

    assert down.calls == 3


def test_a_healthy_call_is_made_once(monkeypatch):
    """The retry must not cost anything when nothing is wrong."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    class Fine:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            return _Completion("OK")

    fine = Fine()
    _provider_with(fine).complete([LLMMessage(role="user", content="go")])

    assert fine.calls == 1


# --- an empty reply ----------------------------------------------------------

def test_an_empty_reply_at_the_token_limit_is_retried_with_room(monkeypatch):
    """This model spends its budget reasoning before emitting a character,
    so a budget that only covers the answer returns "" with full usage
    billed. Downstream that is an unparseable offer, not an error."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    class EmptyThenFine:
        def __init__(self):
            self.calls = 0
            self.budgets = []

        def create(self, max_tokens=None, **kwargs):
            self.calls += 1
            self.budgets.append(max_tokens)
            if self.calls == 1:
                return _Completion("", finish_reason="length")
            return _Completion("PRICE: 2000 agreed")

    stub = EmptyThenFine()

    reply = _provider_with(stub).complete(
        [LLMMessage(role="user", content="go")], max_tokens=800,
    )

    assert reply.text == "PRICE: 2000 agreed"
    assert stub.budgets == [800, 2400], "retried with room to finish"


def test_an_empty_reply_that_stopped_normally_is_not_retried(monkeypatch):
    """Only a budget failure is retried. A model that genuinely answered
    with nothing has answered, and paying twice for that is waste."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    class EmptyButDone:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            return _Completion("", finish_reason="stop")

    stub = EmptyButDone()
    reply = _provider_with(stub).complete([LLMMessage(role="user", content="go")])

    assert reply.text == ""
    assert stub.calls == 1
