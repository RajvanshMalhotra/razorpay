import pytest

from exchange.agents.journal import AgentJournal
from exchange.agents.negotiation import negotiate
from exchange.agents.subconscious import Lesson
from exchange.eventlog import EventLog
from exchange.llm.scripted import ScriptedProvider


@pytest.fixture
def log(tmp_path):
    lg = EventLog(str(tmp_path / "journal.db"))
    yield lg
    lg.close()


def test_recall_injection_is_recorded_with_what_was_recalled(log):
    journal = AgentJournal(log, "m_buyer", "c1")

    journal.recall_injected("m_seller", ("pushes on delivery", "pays late"))

    event = log.read_by_correlation("c1")[0]
    assert event.type == "RECALL_INJECTED"
    assert event.payload["counterparty_id"] == "m_seller"
    assert "pushes on delivery" in event.payload["lessons"]


def test_a_consolidated_lesson_is_recorded_with_its_kind(log):
    journal = AgentJournal(log, "m_buyer", "c1")

    journal.lesson_consolidated(Lesson("m_seller", "packaging", "late twice", "reliability"))

    payload = log.read_by_correlation("c1")[0].payload
    assert payload["kind"] == "reliability"
    assert payload["text"] == "late twice"


def test_every_journal_event_carries_the_correlation_id(log):
    journal = AgentJournal(log, "m_buyer", "c_trade_7")

    journal.recall_injected("m_seller", ())
    journal.negotiation_opened("m_seller", 1940)

    assert all(e.correlation_id == "c_trade_7" for e in log.read_all())


def test_negotiation_writes_open_rounds_and_end(log):
    journal = AgentJournal(log, "m_buyer", "c1")

    negotiate("m_buyer", "m_seller",
              ScriptedProvider(["PRICE: 1900 — our offer."]),
              ScriptedProvider(["PRICE: 1900 — agreed."]),
              opening_price=1940, buyer_limit=2200, seller_floor=1800,
              journal=journal)

    types = [e.type for e in log.read_by_correlation("c1")]
    assert types[0] == "NEGOTIATION_OPENED"
    assert types.count("NEGOTIATION_ROUND") == 2
    assert types[-1] == "NEGOTIATION_ENDED"


def test_the_reason_for_ending_is_recorded(log):
    """This is the signal a round counter could never give."""
    journal = AgentJournal(log, "m_buyer", "c1")

    negotiate("m_buyer", "m_seller",
              ScriptedProvider(["WALK: four rupees is not worth another round."]),
              ScriptedProvider(["PRICE: 2100"]),
              opening_price=2100, buyer_limit=2200, seller_floor=1800,
              journal=journal)

    ended = [e for e in log.read_by_correlation("c1") if e.type == "NEGOTIATION_ENDED"][0]
    assert "walked" in ended.payload["reason"]
    assert ended.payload["agreed"] is False


def test_negotiation_works_without_a_journal():
    """Journalling is optional so the negotiation stays unit-testable."""
    outcome = negotiate("m_buyer", "m_seller",
                        ScriptedProvider(["PRICE: 1900"]),
                        ScriptedProvider(["PRICE: 1900"]),
                        opening_price=1940, buyer_limit=2200, seller_floor=1800)

    assert outcome.agreed is True
