from dataclasses import replace

import pytest

from exchange.eventlog import EventLog
from exchange.events import (
    ASSET_LISTED,
    MATCH_PROPOSED,
    ORDER_FILLED,
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
        ORDER_FILLED, ORDER_FILLED,
    ]


def test_an_unknown_counterparty_is_bounded_not_excluded(exchange):
    """The trial-size rule: the trade happens, but only a small one.

    A 200-unit lot is inside the 500,000 cap an unknown counterparty gets; the
    same lot at 500 units is not. Both are offered by the matcher — the bound
    limits the size of a first trade rather than excluding the counterparty.
    """
    asks = _seed_market(exchange)
    bid = Order(
        order_id="ord_bid", actor_id="m_buyer", side=Side.BID, asset_ref=None,
        asset_query={"text": "biodegradable compostable mailers"}, qty=200,
        limit_price=2200, currency=Currency.INR,
        expires_at="2026-08-29T00:00:00+00:00", policy_snapshot={},
    )
    matches = find_candidates(bid, asks, exchange.state().assets, exchange.index)
    assert matches, "an unknown counterparty must still be offered"

    unknown_ctx = PolicyContext(ActorStatus.ACTIVE, 0, counterparty_confidence=0.05)
    decision, _ = exchange.execute_match(
        matches[0], "m_buyer", "m_unknown", unknown_ctx, correlation_id=CORR
    )

    assert decision.verdict == Verdict.ALLOW
    # The gate weighed the whole lot, not one unit's price.
    assert decision.limits_evaluated["amount"] == matches[0].clearing_price * 200
    assert decision.limits_evaluated["amount"] < 500_000

    # And the same bid one size up is refused, so the cap is genuinely binding.
    big = find_candidates(
        replace(bid, order_id="ord_bid_big", qty=500), asks,
        exchange.state().assets, exchange.index,
    )
    refused, _ = exchange.execute_match(
        big[0], "m_buyer", "m_unknown", unknown_ctx, correlation_id=CORR
    )
    assert refused.verdict == Verdict.DENY
    assert "unknown counterparty cap" in refused.reason


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

    _assert_every_settlement_traces_to_its_own_allow(exchange.log.read_all())


def _assert_every_settlement_traces_to_its_own_allow(events):
    """Correlate each settlement to the decision that gated *it*.

    Taking the most recent POLICY_DECIDED anywhere in the log is not the
    invariant: with two interleaved matches a DENY on A followed by an ALLOW on
    B would let a settlement for A pass the check. The link is action_ref.
    """
    checked = 0
    for i, event in enumerate(events):
        if event.type != SETTLEMENT_INITIATED:
            continue
        match_id = event.payload["match_id"]
        gating = [
            (j, e)
            for j, e in enumerate(events)
            if e.type == POLICY_DECIDED and e.payload["action_ref"] == match_id
        ]
        assert gating, f"settlement for {match_id} has no decision referencing it"
        j, decision = gating[-1]
        assert j < i, f"decision for {match_id} does not precede its settlement"
        assert decision.payload["verdict"] == "ALLOW"
        checked += 1
    return checked


def test_a_deny_on_another_match_cannot_satisfy_the_allow_invariant(exchange):
    """The weakness the previous version of the check missed.

    A DENY on match A, then an ALLOW on match B, then a settlement for B. The
    uncorrelated check passed this by reading the latest decision; the
    correlated one has to find B's own ALLOW, and must not be fooled into
    treating A as settled.
    """
    asks = _seed_market(exchange)
    bid = Order(
        order_id="ord_bid", actor_id="m_buyer", side=Side.BID, asset_ref=None,
        asset_query={"text": "biodegradable compostable mailers"}, qty=500,
        limit_price=2200, currency=Currency.INR,
        expires_at="2026-08-29T00:00:00+00:00", policy_snapshot={},
    )
    matches = find_candidates(bid, asks, exchange.state().assets, exchange.index, top_k=2)
    match_a, match_b = matches[0], matches[1]

    frozen = PolicyContext(ActorStatus.FROZEN, 0, 0.9)
    denied, no_settlement = exchange.execute_match(
        match_a, "m_buyer", "m_unknown", frozen, correlation_id=CORR
    )
    assert denied.verdict == Verdict.DENY
    assert no_settlement is None

    allowed, settlement = exchange.execute_match(
        match_b, "m_buyer", "m_known",
        PolicyContext(ActorStatus.ACTIVE, 0, 0.9), correlation_id=CORR,
    )
    assert allowed.verdict == Verdict.ALLOW
    assert settlement is not None

    events = exchange.log.read_all()
    assert _assert_every_settlement_traces_to_its_own_allow(events) == 1

    # And confirm the check is genuinely correlating rather than passing vacuously.
    denials = [
        e for e in events
        if e.type == POLICY_DECIDED
        and e.payload["action_ref"] == match_a.match_id
        and e.payload["verdict"] == "DENY"
    ]
    assert len(denials) == 1
    settled_matches = {
        e.payload["match_id"] for e in events if e.type == SETTLEMENT_INITIATED
    }
    assert match_a.match_id not in settled_matches
    assert settled_matches == {match_b.match_id}

    # The discriminating case. Repoint the settlement at the DENIED match A.
    # The uncorrelated check read the latest decision — still ALLOW(B) — and
    # passed. The correlated one has to find A's own verdict, and it is DENY.
    tampered = [
        replace(e, payload={**e.payload, "match_id": match_a.match_id})
        if e.type == SETTLEMENT_INITIATED else e
        for e in events
    ]
    with pytest.raises(AssertionError):
        _assert_every_settlement_traces_to_its_own_allow(tampered)


def test_a_settled_trade_depletes_both_sides_of_the_book(exchange):
    """Without this the same ask re-matches forever, settling real money each time."""
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

    book = exchange.state().open_orders
    assert "ord_bid" not in book, "the bid was filled in full"
    assert book["ord_ask_mailers"].qty == 500, "1000 offered less 500 taken"
    assert book["ord_ask_boxes"].qty == 1000, "the untraded ask is untouched"


def test_a_denied_match_leaves_the_book_untouched(exchange):
    asks = _seed_market(exchange)
    bid = Order(
        order_id="ord_bid", actor_id="m_buyer", side=Side.BID, asset_ref=None,
        asset_query={"text": "biodegradable compostable mailers"}, qty=500,
        limit_price=2200, currency=Currency.INR,
        expires_at="2026-08-29T00:00:00+00:00", policy_snapshot={},
    )
    exchange.post_order(bid, correlation_id=CORR)
    matches = find_candidates(bid, asks, exchange.state().assets, exchange.index)

    decision, _ = exchange.execute_match(
        matches[0], "m_buyer", "m_unknown",
        PolicyContext(ActorStatus.FROZEN, 0, 0.9), correlation_id=CORR,
    )

    assert decision.verdict == Verdict.DENY
    book = exchange.state().open_orders
    assert book["ord_bid"].qty == 500
    assert book["ord_ask_mailers"].qty == 1000
    assert ORDER_FILLED not in [e.type for e in exchange.log.read_all()]


def test_state_folded_from_the_log_matches_live_state(exchange):
    _seed_market(exchange)

    from exchange.projections import fold

    assert fold(exchange.log.read_all()) == exchange.state()
