import pytest

from exchange.eventlog import EventLog
from exchange.events import POLICY_DECIDED, SETTLEMENT_COMPLETED, SETTLEMENT_INITIATED
from exchange.models import (
    Actor,
    ActorKind,
    ActorStatus,
    Asset,
    AssetKind,
    Currency,
    Match,
    Order,
    Side,
    Verdict,
)
from exchange.policy import DEFAULT_INR_LIMITS, PolicyContext
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex
from exchange.service import Exchange
from tests.test_rails import FakeRazorpay
from tests.test_retrieval import fake_embedder

TRUSTED = PolicyContext(
    actor_status=ActorStatus.ACTIVE, rolling_spend=0, counterparty_confidence=0.9
)


@pytest.fixture
def exchange(tmp_path):
    log = EventLog(str(tmp_path / "svc.db"))
    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_abc", "status": "captured"}]}
    })
    index = HybridIndex(embed_fn=fake_embedder)
    ex = Exchange(log, index, RazorpayRail(log, fake), CreditRail(log))
    yield ex
    log.close()


MATCH = Match(
    match_id="mch_1",
    bid_order_id="ord_bid",
    ask_order_id="ord_ask",
    clearing_price=1940,
    score=0.9,
    rationale="test",
)


def test_registering_an_actor_puts_it_in_state(exchange):
    exchange.register_actor(Actor(actor_id="m_a", kind=ActorKind.MERCHANT))

    assert exchange.state().actors["m_a"].kind == ActorKind.MERCHANT


def test_listing_an_asset_puts_it_in_state_and_the_index(exchange):
    exchange.list_asset(Asset(
        asset_id="ast_1",
        kind=AssetKind.GOODS,
        title="biodegradable mailers",
        spec={},
        currency=Currency.INR,
        origin_actor_id="m_b",
    ))

    assert exchange.state().assets["ast_1"].title == "biodegradable mailers"
    assert exchange.index.search("mailers")[0][0] == "ast_1"


def test_posting_an_order_puts_it_in_the_book(exchange):
    order = Order(
        order_id="ord_1",
        actor_id="m_a",
        side=Side.BID,
        asset_ref=None,
        asset_query={"text": "mailers"},
        qty=500,
        limit_price=2200,
        currency=Currency.INR,
        expires_at="2026-09-30T00:00:00+00:00",
        policy_snapshot={},
    )

    exchange.post_order(order, correlation_id="c1")

    assert "ord_1" in exchange.state().open_orders


def test_allowed_match_settles_and_logs_the_decision_first(exchange):
    decision, settlement = exchange.execute_match(
        MATCH, "m_buyer", "m_seller", TRUSTED, correlation_id="c1"
    )

    assert decision.verdict == Verdict.ALLOW
    assert settlement is not None
    types = [e.type for e in exchange.log.read_by_correlation("c1")]
    assert types.index(POLICY_DECIDED) < types.index(SETTLEMENT_INITIATED)


def test_a_policy_decision_is_logged_even_when_the_verdict_is_allow(exchange):
    exchange.execute_match(MATCH, "m_buyer", "m_seller", TRUSTED, correlation_id="c1")

    decided = [e for e in exchange.log.read_by_correlation("c1") if e.type == POLICY_DECIDED]

    assert len(decided) == 1
    assert decided[0].payload["verdict"] == "ALLOW"


def test_denied_match_logs_the_decision_and_moves_no_money(exchange):
    frozen = PolicyContext(
        actor_status=ActorStatus.FROZEN, rolling_spend=0, counterparty_confidence=0.9
    )

    decision, settlement = exchange.execute_match(
        MATCH, "m_buyer", "m_seller", frozen, correlation_id="c1"
    )

    assert decision.verdict == Verdict.DENY
    assert settlement is None
    types = [e.type for e in exchange.log.read_by_correlation("c1")]
    assert SETTLEMENT_INITIATED not in types


def test_match_requiring_human_approval_does_not_settle(exchange):
    big = Match(
        match_id="mch_2",
        bid_order_id="ord_bid",
        ask_order_id="ord_ask",
        clearing_price=DEFAULT_INR_LIMITS.human_approval_threshold + 1,
        score=0.9,
        rationale="test",
    )

    decision, settlement = exchange.execute_match(
        big, "m_buyer", "m_seller", TRUSTED, correlation_id="c1"
    )

    assert decision.verdict == Verdict.REQUIRE_HUMAN
    assert settlement is None


def test_the_whole_story_is_recoverable_from_one_correlation_id(exchange):
    exchange.execute_match(MATCH, "m_buyer", "m_seller", TRUSTED, correlation_id="c1")

    types = [e.type for e in exchange.log.read_by_correlation("c1")]

    assert types == [POLICY_DECIDED, SETTLEMENT_INITIATED, SETTLEMENT_COMPLETED]
