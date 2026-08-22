import pytest

from exchange.eventlog import EventLog
from exchange.events import (
    ASSET_LISTED,
    MATCH_PROPOSED,
    ORDER_POSTED,
    POLICY_DECIDED,
    SETTLEMENT_COMPLETED,
    SETTLEMENT_INITIATED,
)
from exchange.matching import find_candidates
from exchange.models import (
    Actor,
    ActorKind,
    ActorStatus,
    Asset,
    AssetKind,
    Currency,
    Order,
    SettlementStatus,
    Side,
    Verdict,
)
from exchange.policy import PolicyContext
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex
from exchange.service import Exchange
from tests.test_rails import FakeRazorpay
from tests.test_retrieval import fake_embedder

CORR = "corr_vitaminc_launch"


@pytest.fixture
def exchange(tmp_path):
    log = EventLog(str(tmp_path / "e2e.db"))
    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_e2e", "status": "captured"}]}
    })
    ex = Exchange(
        log,
        HybridIndex(embed_fn=fake_embedder),
        RazorpayRail(log, fake),
        CreditRail(log),
    )
    yield ex
    log.close()


def _seed_market(exchange):
    for actor_id in ("m_buyer", "m_known", "m_unknown"):
        exchange.register_actor(Actor(actor_id=actor_id, kind=ActorKind.MERCHANT))

    exchange.list_asset(Asset(
        asset_id="ast_boxes", kind=AssetKind.GOODS,
        title="corrugated kraft boxes recyclable", spec={},
        currency=Currency.INR, origin_actor_id="m_known",
    ))
    exchange.list_asset(Asset(
        asset_id="ast_mailers", kind=AssetKind.GOODS,
        title="biodegradable mailers compostable poly", spec={},
        currency=Currency.INR, origin_actor_id="m_unknown",
    ))

    asks = [
        Order(
            order_id="ord_ask_boxes", actor_id="m_known", side=Side.ASK,
            asset_ref="ast_boxes", asset_query=None, qty=1000, limit_price=1800,
            currency=Currency.INR, expires_at="2026-09-30T00:00:00+00:00",
            policy_snapshot={},
        ),
        Order(
            order_id="ord_ask_mailers", actor_id="m_unknown", side=Side.ASK,
            asset_ref="ast_mailers", asset_query=None, qty=1000, limit_price=1940,
            currency=Currency.INR, expires_at="2026-09-30T00:00:00+00:00",
            policy_snapshot={},
        ),
    ]
    for ask in asks:
        exchange.post_order(ask, correlation_id=CORR)
    return asks


def test_descriptive_bid_settles_end_to_end(exchange):
    asks = _seed_market(exchange)

    bid = Order(
        order_id="ord_bid", actor_id="m_buyer", side=Side.BID,
        asset_ref=None,
        asset_query={"text": "biodegradable compostable mailers", "by": "2026-08-29"},
        qty=500, limit_price=2200, currency=Currency.INR,
        expires_at="2026-08-29T00:00:00+00:00", policy_snapshot={},
    )
    exchange.post_order(bid, correlation_id=CORR)

    matches = find_candidates(bid, asks, exchange.state().assets, exchange.index)
    assert matches[0].ask_order_id == "ord_ask_mailers"

    decision, settlement = exchange.execute_match(
        matches[0], "m_buyer", "m_unknown",
        PolicyContext(ActorStatus.ACTIVE, rolling_spend=0, counterparty_confidence=0.9),
        correlation_id=CORR,
    )

    assert decision.verdict == Verdict.ALLOW
    assert settlement.status == SettlementStatus.COMPLETED
    assert settlement.razorpay_payment_id == "pay_e2e"


def test_the_full_story_reads_back_from_one_correlation_id(exchange):
    asks = _seed_market(exchange)
    bid = Order(
        order_id="ord_bid", actor_id="m_buyer", side=Side.BID, asset_ref=None,
        asset_query={"text": "biodegradable compostable mailers"}, qty=500,
        limit_price=2200, currency=Currency.INR,
        expires_at="2026-08-29T00:00:00+00:00", policy_snapshot={},
    )
    exchange.post_order(bid, correlation_id=CORR)
    matches = find_candidates(bid, asks, exchange.state().assets, exchange.index)
    exchange.execute_match(
        matches[0], "m_buyer", "m_unknown",
        PolicyContext(ActorStatus.ACTIVE, 0, 0.9), correlation_id=CORR,
    )

    types = [e.type for e in exchange.log.read_by_correlation(CORR)]

    assert types == [
        ORDER_POSTED, ORDER_POSTED, ORDER_POSTED,
        MATCH_PROPOSED, POLICY_DECIDED, SETTLEMENT_INITIATED, SETTLEMENT_COMPLETED,
    ]


def test_an_unknown_counterparty_is_bounded_not_excluded(exchange):
    """The trial-size rule: the trade happens, but only a small one."""
    asks = _seed_market(exchange)
    bid = Order(
        order_id="ord_bid", actor_id="m_buyer", side=Side.BID, asset_ref=None,
        asset_query={"text": "biodegradable compostable mailers"}, qty=500,
        limit_price=2200, currency=Currency.INR,
        expires_at="2026-08-29T00:00:00+00:00", policy_snapshot={},
    )
    matches = find_candidates(
        bid, asks, exchange.state().assets, exchange.index,
        counterparty_scores={"m_unknown": 0.0},
    )
    assert matches, "an unknown counterparty must still be offered"

    unknown_ctx = PolicyContext(ActorStatus.ACTIVE, 0, counterparty_confidence=0.05)
    decision, _ = exchange.execute_match(
        matches[0], "m_buyer", "m_unknown", unknown_ctx, correlation_id=CORR
    )

    assert decision.verdict == Verdict.ALLOW  # 1940 is under the 500_000 trial cap


def test_every_settlement_is_preceded_by_an_allow_decision(exchange):
    """The invariant the accountant will later assert globally."""
    asks = _seed_market(exchange)
    bid = Order(
        order_id="ord_bid", actor_id="m_buyer", side=Side.BID, asset_ref=None,
        asset_query={"text": "biodegradable compostable mailers"}, qty=500,
        limit_price=2200, currency=Currency.INR,
        expires_at="2026-08-29T00:00:00+00:00", policy_snapshot={},
    )
    matches = find_candidates(bid, asks, exchange.state().assets, exchange.index)
    exchange.execute_match(
        matches[0], "m_buyer", "m_unknown",
        PolicyContext(ActorStatus.ACTIVE, 0, 0.9), correlation_id=CORR,
    )

    events = exchange.log.read_all()
    for i, event in enumerate(events):
        if event.type == SETTLEMENT_INITIATED:
            preceding = [e for e in events[:i] if e.type == POLICY_DECIDED]
            assert preceding, "settlement with no preceding decision"
            assert preceding[-1].payload["verdict"] == "ALLOW"


def test_state_folded_from_the_log_matches_live_state(exchange):
    _seed_market(exchange)

    from exchange.projections import fold

    assert fold(exchange.log.read_all()) == exchange.state()
