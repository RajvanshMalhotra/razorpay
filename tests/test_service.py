import pytest

from exchange.eventlog import EventLog
from exchange.events import (
    ACTOR_FROZEN,
    ACTOR_RESUMED,
    CREDITS_TRANSFERRED,
    MATCH_PROPOSED,
    POLICY_DECIDED,
    SETTLEMENT_COMPLETED,
    SETTLEMENT_INITIATED,
)
from exchange.models import (
    Actor,
    ActorKind,
    ActorStatus,
    Asset,
    AssetKind,
    Currency,
    Match,
    Order,
    SettlementStatus,
    Side,
    Verdict,
)
from exchange.policy import DEFAULT_INR_LIMITS, PolicyContext, PolicyLimits
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
    qty=500,
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
    """The freeze is in the log, not in the argument.

    This test used to hand the gate a FROZEN context, which proved only that
    `policy.evaluate` reads the flag. The actor is frozen in the log here and
    the caller still claims ACTIVE — the DENY has to come from the projection.
    """
    exchange.register_actor(Actor(actor_id="m_buyer", kind=ActorKind.MERCHANT))
    exchange.log.append("accountant", ACTOR_FROZEN,
                        {"actor_id": "m_buyer", "reason": "books disagree"},
                        correlation_id="freeze_m_buyer")

    decision, settlement = exchange.execute_match(
        MATCH, "m_buyer", "m_seller", TRUSTED, correlation_id="c1"
    )

    assert decision.verdict == Verdict.DENY
    assert "frozen" in decision.reason.lower()
    assert decision.limits_evaluated["actor_status"] == "FROZEN"
    assert settlement is None
    types = [e.type for e in exchange.log.read_by_correlation("c1")]
    assert SETTLEMENT_INITIATED not in types


def test_the_gate_sees_the_whole_lot_not_one_unit(exchange):
    """A 500-unit trade must be gated on 500 units of exposure."""
    match = Match(match_id="mch_1", bid_order_id="ord_bid", ask_order_id="ord_ask",
                  clearing_price=1940, qty=500, score=0.9, rationale="test")

    decision, _ = exchange.execute_match(match, "m_buyer", "m_seller", TRUSTED,
                                         correlation_id="c1")

    assert decision.limits_evaluated["amount"] == 970_000


def test_match_requiring_human_approval_does_not_settle(exchange):
    # 3200 per unit over 500 units is 1,600,000: at or above the 1,500,000
    # human-approval threshold and still under the 2,000,000 per-transaction
    # cap, so REQUIRE_HUMAN is the level that binds rather than DENY.
    big = Match(
        match_id="mch_2",
        bid_order_id="ord_bid",
        ask_order_id="ord_ask",
        clearing_price=3200,
        qty=500,
        score=0.9,
        rationale="test",
    )
    assert (
        DEFAULT_INR_LIMITS.human_approval_threshold
        <= big.clearing_price * big.qty
        <= DEFAULT_INR_LIMITS.per_txn_cap
    )

    decision, settlement = exchange.execute_match(
        big, "m_buyer", "m_seller", TRUSTED, correlation_id="c1"
    )

    assert decision.verdict == Verdict.REQUIRE_HUMAN
    assert settlement is None


def test_the_rolling_cap_is_derived_from_the_log_not_from_the_caller(tmp_path):
    """The caller passes rolling_spend=0 both times; the second trade is still
    denied, because the gate counts what the log says was already spent."""
    log = EventLog(str(tmp_path / "roll.db"))
    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_1", "status": "captured"}]},
        "order_2": {"count": 1, "items": [{"id": "pay_2", "status": "captured"}]},
    })
    tight = PolicyLimits(
        per_txn_cap=200_000,
        rolling_window_cap=250_000,   # one trade fits; two do not
        human_approval_threshold=1_000_000_000,
        unknown_counterparty_cap=200_000,
    )
    ex = Exchange(
        log, HybridIndex(embed_fn=fake_embedder),
        RazorpayRail(log, fake), CreditRail(log), inr_limits=tight,
    )

    def trade(match_id):
        # 300 per unit over 500 units is 150,000 — one trade fits inside both
        # the 200,000 per-transaction cap and the 250,000 window; two do not.
        return ex.execute_match(
            Match(match_id, "ord_bid", "ord_ask", 300, 500, 0.9, "test"),
            "m_buyer", "m_seller", TRUSTED, correlation_id="c1",
        )

    first_decision, first_settlement = trade("mch_1")
    assert first_decision.verdict == Verdict.ALLOW
    assert first_settlement.status == SettlementStatus.COMPLETED

    second_decision, second_settlement = trade("mch_2")

    assert second_decision.verdict == Verdict.DENY
    assert "rolling window" in second_decision.reason
    assert second_decision.limits_evaluated["rolling_spend"] == 150_000
    assert second_settlement is None

    initiated = [e for e in log.read_all() if e.type == SETTLEMENT_INITIATED]
    assert len(initiated) == 1, "the second settlement must never have started"
    log.close()


def test_rolling_spend_is_counted_per_currency(tmp_path):
    """An INR settlement must not consume a buyer's CREDITS headroom."""
    log = EventLog(str(tmp_path / "cur.db"))
    log.append("house", CREDITS_TRANSFERRED,
               {"from_actor_id": "house", "to_actor_id": "m_buyer", "amount": 500_000},
               correlation_id="seed")
    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_1", "status": "captured"}]}
    })
    tight = PolicyLimits(
        per_txn_cap=200_000,
        rolling_window_cap=250_000,
        human_approval_threshold=1_000_000_000,
        unknown_counterparty_cap=200_000,
    )
    ex = Exchange(
        log, HybridIndex(embed_fn=fake_embedder),
        RazorpayRail(log, fake), CreditRail(log),
        inr_limits=tight, credit_limits=tight,
    )

    # 300 per unit over 500 units is 150,000 on each rail: the INR trade uses
    # more than half the shared window figure, so a leak across currencies
    # would deny the CREDITS trade that follows.
    ex.execute_match(
        Match("mch_inr", "ord_bid", "ord_ask", 300, 500, 0.9, "test"),
        "m_buyer", "m_seller", TRUSTED, correlation_id="c1",
    )
    decision, _ = ex.execute_match(
        Match("mch_cr", "ord_bid", "ord_ask", 300, 500, 0.9, "test"),
        "m_buyer", "m_seller", TRUSTED, correlation_id="c1",
        currency=Currency.CREDITS,
    )

    assert decision.verdict == Verdict.ALLOW
    assert decision.limits_evaluated["rolling_spend"] == 0
    log.close()


def test_the_decision_is_caused_by_the_match_it_gated(exchange):
    """action_ref must not dangle: the match is a logged event that caused the gate."""
    exchange.execute_match(MATCH, "m_buyer", "m_seller", TRUSTED, correlation_id="c1")

    events = exchange.log.read_by_correlation("c1")
    proposed = [e for e in events if e.type == MATCH_PROPOSED][0]
    decided = [e for e in events if e.type == POLICY_DECIDED][0]

    assert proposed.payload["match_id"] == "mch_1"
    assert decided.causation_id == proposed.event_id
    assert decided.payload["action_ref"] == proposed.payload["match_id"]
    assert exchange.state().matches["mch_1"].rationale == "test"


def test_the_whole_story_is_recoverable_from_one_correlation_id(exchange):
    exchange.execute_match(MATCH, "m_buyer", "m_seller", TRUSTED, correlation_id="c1")

    types = [e.type for e in exchange.log.read_by_correlation("c1")]

    assert types == [
        MATCH_PROPOSED, POLICY_DECIDED, SETTLEMENT_INITIATED, SETTLEMENT_COMPLETED,
    ]


def test_fill_is_recorded_for_the_ask_even_when_the_bid_is_not_in_the_book(exchange):
    """The ask must still be depleted, or it can be re-settled forever."""
    ask = Order(
        order_id="ord_ask", actor_id="m_seller", side=Side.ASK, asset_ref="ast_1",
        asset_query=None, qty=1000, limit_price=1940, currency=Currency.INR,
        expires_at="2026-09-30T00:00:00+00:00", policy_snapshot={},
    )
    exchange.post_order(ask, correlation_id="c1")
    # note: the bid is deliberately NOT posted

    match = Match(
        match_id="mch_1", bid_order_id="ord_absent_bid", ask_order_id="ord_ask",
        clearing_price=1940, qty=400, score=0.9, rationale="test",
    )
    exchange.execute_match(match, "m_buyer", "m_seller", TRUSTED, correlation_id="c1")

    assert exchange.state().open_orders["ord_ask"].qty == 600


def test_each_side_of_a_fill_is_attributed_to_its_own_actor(exchange):
    bid = Order(
        order_id="ord_bid", actor_id="m_buyer", side=Side.BID, asset_ref=None,
        asset_query={"text": "mailers"}, qty=400, limit_price=2200,
        currency=Currency.INR, expires_at="2026-09-30T00:00:00+00:00",
        policy_snapshot={},
    )
    ask = Order(
        order_id="ord_ask", actor_id="m_seller", side=Side.ASK, asset_ref="ast_1",
        asset_query=None, qty=1000, limit_price=1940, currency=Currency.INR,
        expires_at="2026-09-30T00:00:00+00:00", policy_snapshot={},
    )
    exchange.post_order(bid, correlation_id="c1")
    exchange.post_order(ask, correlation_id="c1")

    match = Match(
        match_id="mch_1", bid_order_id="ord_bid", ask_order_id="ord_ask",
        clearing_price=1940, qty=400, score=0.9, rationale="test",
    )
    exchange.execute_match(match, "m_buyer", "m_seller", TRUSTED, correlation_id="c1")

    fills = [e for e in exchange.log.read_by_correlation("c1") if e.type == "ORDER_FILLED"]
    by_order = {e.payload["order_id"]: e for e in fills}

    assert by_order["ord_bid"].actor_id == "m_buyer"
    assert by_order["ord_ask"].actor_id == "m_seller"
    assert by_order["ord_bid"].payload["qty"] == 400
    assert by_order["ord_ask"].payload["qty"] == 400
