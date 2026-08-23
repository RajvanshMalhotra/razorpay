import pytest

from exchange.agents.broker import Broker
from exchange.agents.context import ContextState
from exchange.agents.negotiation import negotiate
from exchange.eventlog import EventLog
from exchange.llm.scripted import ScriptedProvider
from exchange.models import (
    Actor, ActorKind, Asset, AssetKind, Currency, Order, Side, Verdict,
)
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex
from exchange.service import Exchange
from tests.test_rails import FakeRazorpay
from tests.test_retrieval import fake_embedder

CORR = "corr_two_brokers"


@pytest.fixture
def exchange(tmp_path):
    log = EventLog(str(tmp_path / "e2e.db"))
    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_e2e", "status": "captured"}]}
    })
    ex = Exchange(log, HybridIndex(embed_fn=fake_embedder),
                  RazorpayRail(log, fake, poll_attempts=1, poll_interval=0),
                  CreditRail(log))
    for actor_id in ("m_buyer", "m_seller"):
        ex.register_actor(Actor(actor_id=actor_id, kind=ActorKind.MERCHANT))
    ex.list_asset(Asset(asset_id="ast_mailers", kind=AssetKind.GOODS,
                        title="biodegradable mailers compostable poly", spec={},
                        currency=Currency.INR, origin_actor_id="m_seller"))
    ex.post_order(Order(order_id="ord_ask", actor_id="m_seller", side=Side.ASK,
                        asset_ref="ast_mailers", asset_query=None, qty=1000,
                        limit_price=1940, currency=Currency.INR,
                        expires_at="2026-09-30T00:00:00+00:00", policy_snapshot={}),
                  correlation_id=CORR)
    yield ex
    log.close()


def test_two_brokers_negotiate_and_settle(exchange):
    buyer = Broker("m_buyer", exchange, ScriptedProvider(["candidates found", "advice"]))

    # 200 units: the first deal with a stranger has to fit the trial cap.
    matches = buyer.find_supply("biodegradable compostable mailers", 200, 2200, CORR)
    assert matches

    outcome = negotiate(
        "m_buyer", "m_seller",
        ScriptedProvider(["PRICE: 1900 — that is our offer."]),
        ScriptedProvider(["PRICE: 1900 — agreed."]),
        opening_price=1940, buyer_limit=2200, seller_floor=1800,
    )
    assert outcome.agreed is True

    decision, settlement = buyer.close(
        matches[0], "m_seller", CORR, agreed_price=outcome.final_price,
    )

    assert decision.verdict == Verdict.ALLOW
    assert settlement is not None
    assert settlement.amount == 1900 * 200, "settled at what was agreed, not the ask"


def test_the_second_deal_is_informed_by_the_first(exchange):
    """The whole point of the Subconscious."""
    provider = ScriptedProvider([
        "candidates found",
        "RELIABILITY: delivered two days late",
        "given the late delivery last time, hold firm on terms",
    ])
    buyer = Broker("m_buyer", exchange, provider)
    buyer.find_supply("biodegradable compostable mailers", 500, 2200, CORR)

    buyer.subconscious.consolidate(
        ContextState(facts=("settled at 1940", "delivery slipped")),
        "m_seller", "packaging",
    )
    advice = buyer.assess("m_seller", CORR)

    assert "late delivery" in advice
    assert "delivered two days late" in provider.calls[-1]["messages"][0].content


def test_a_reliability_lesson_lowers_standing_but_never_excludes(exchange):
    buyer = Broker("m_buyer", exchange, ScriptedProvider(["RELIABILITY: no show", "c"]))
    buyer.graph.record_deal("m_seller", value=100_000, delivered=True)
    before = buyer.graph.standing("m_seller")

    lesson = buyer.subconscious.consolidate(
        ContextState(facts=("did not arrive",)), "m_seller", "packaging",
    )
    buyer.graph.apply_lesson(lesson)

    assert buyer.graph.standing("m_seller") < before
    matches = buyer.find_supply("biodegradable compostable mailers", 500, 2200, CORR)
    assert matches, "a poor counterparty must still be offered, only bounded"


def test_the_whole_story_threads_one_correlation_id(exchange):
    buyer = Broker("m_buyer", exchange, ScriptedProvider(
        ["c", "BEHAVIOURAL: moved fast on volume"]))
    # 200 units: the first deal with a stranger has to fit the trial cap, or
    # the gate denies it and there is no settlement leg of the story to read.
    matches = buyer.find_supply("biodegradable compostable mailers", 200, 2200, CORR)
    buyer.close(matches[0], "m_seller", CORR, agreed_price=matches[0].clearing_price)

    types = [e.type for e in exchange.log.read_by_correlation(CORR)]

    assert "ORDER_POSTED" in types
    assert types.index("MATCH_PROPOSED") < types.index("POLICY_DECIDED")
    assert types.index("POLICY_DECIDED") < types.index("SETTLEMENT_INITIATED")
    assert "ORDER_FILLED" in types
