from datetime import datetime, timedelta, timezone

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


# --- The gate is authoritative, not advised -------------------------------
#
# Three regressions for one root cause: a value the gate must be authoritative
# about was being supplied by the party it constrains. Each of these failed
# before the fix, and each would fail silently — with money moving — rather
# than raising.


def _frozen_buyer(exchange):
    """A registered, then frozen, merchant and a counterparty to trade with."""
    from exchange.house.accountant import Accountant

    exchange.register_actor(Actor(actor_id="m_buyer", kind=ActorKind.MERCHANT))
    exchange.register_actor(Actor(actor_id="m_seller", kind=ActorKind.MERCHANT))
    Accountant(exchange.log, FakeRazorpay()).freeze("m_buyer", "books disagree")


def test_a_frozen_actor_is_denied_even_though_its_broker_claims_active(exchange):
    """The freeze must bind the caller that has every reason to ignore it.

    TRUSTED carries actor_status=ACTIVE. That is not an artificial test
    setup — it is exactly what the broker sent in production, which is why
    the freeze was decorative: the gate checked FROZEN and never saw one.
    """
    _frozen_buyer(exchange)

    decision, settlement = exchange.execute_match(
        MATCH, "m_buyer", "m_seller", TRUSTED, correlation_id="c1",
    )

    assert decision.verdict == Verdict.DENY
    assert "frozen" in decision.reason.lower()
    assert settlement is None
    # The gate refusing is not enough; nothing may reach the rail.
    types = [e.type for e in exchange.log.read_all()]
    assert "SETTLEMENT_INITIATED" not in types


def test_a_resumed_actor_may_trade_again(exchange):
    """The freeze lifts, or it is a ban rather than a hold."""
    from exchange.house.accountant import Accountant

    _frozen_buyer(exchange)
    Accountant(exchange.log, FakeRazorpay()).resume("m_buyer")

    decision, _ = exchange.execute_match(
        MATCH, "m_buyer", "m_seller", TRUSTED, correlation_id="c1",
    )

    assert decision.verdict == Verdict.ALLOW


def test_a_retry_at_a_smaller_size_gets_its_own_match_id(exchange):
    """A DENY and a later ALLOW must never share an action_ref.

    The accountant joins settlements to decisions on match_id precisely so a
    refused match and an allowed one on the same correlation cannot be
    confused. `replace(match, qty=...)` preserved the id and reopened that
    hole from the inside.
    """
    from exchange.matching import resize

    exchange.register_actor(Actor(actor_id="m_buyer", kind=ActorKind.MERCHANT))
    exchange.register_actor(Actor(actor_id="m_seller", kind=ActorKind.MERCHANT))
    unknown = PolicyContext(
        actor_status=ActorStatus.ACTIVE, rolling_spend=0, counterparty_confidence=0.0,
    )

    denied, _ = exchange.execute_match(
        MATCH, "m_buyer", "m_seller", unknown, correlation_id="c1",
    )
    allowed, _ = exchange.execute_match(
        resize(MATCH, qty=200), "m_buyer", "m_seller", unknown, correlation_id="c1",
    )

    assert denied.verdict == Verdict.DENY
    assert allowed.verdict == Verdict.ALLOW
    assert denied.action_ref != allowed.action_ref


def test_reusing_a_decided_match_id_is_refused_before_anything_is_written(exchange):
    """Caller bug, not a market event — so it raises rather than logging."""
    from dataclasses import replace

    exchange.register_actor(Actor(actor_id="m_buyer", kind=ActorKind.MERCHANT))
    exchange.register_actor(Actor(actor_id="m_seller", kind=ActorKind.MERCHANT))
    exchange.execute_match(MATCH, "m_buyer", "m_seller", TRUSTED, correlation_id="c1")
    before = len(exchange.log.read_all())

    with pytest.raises(ValueError, match="fresh match_id"):
        exchange.execute_match(
            replace(MATCH, qty=200), "m_buyer", "m_seller", TRUSTED,
            correlation_id="c1",
        )

    assert len(exchange.log.read_all()) == before


# --- the mint basis is a price somebody else asked -------------------------
#
# The fifth instance of the same root cause, in code the earlier fixes added.
# `points_for_settlement` pays a proportion of `ask_price * qty - amount`, and
# `ask_price` was read from whatever ORDER_POSTED matched `match.ask_order_id` —
# a field on the Match the BUYER hands to `execute_match`. Reading an
# authoritative log at a row the constrained party names is the same defect as
# taking the figure from the argument, with one extra hop.
#
# Every one of these mints millions against the pre-fix code, with the gate
# saying ALLOW and `assert_invariants()` returning [].


def _ask(exchange, order_id, actor_id, limit_price, qty=1, side=Side.ASK,
         correlation_id="c1"):
    exchange.post_order(Order(
        order_id=order_id, actor_id=actor_id, side=side,
        asset_ref="ast_1" if side == Side.ASK else None,
        asset_query=None if side == Side.ASK else {"text": "mailers"},
        qty=qty, limit_price=limit_price, currency=Currency.INR,
        expires_at="2026-12-31T00:00:00+00:00", policy_snapshot={},
    ), correlation_id=correlation_id)


def _minted(exchange, actor_id="m_buyer"):
    return sum(
        e.payload["points"] for e in exchange.log.read_all()
        if e.type == "POINTS_MINTED" and e.payload["actor_id"] == actor_id
    )


def test_a_merchant_cannot_mint_against_an_ask_it_posted_to_itself(exchange):
    """The reproduction, verbatim: post an ASK to yourself at an absurd limit,
    settle one paisa against it, collect five million points.

    The gate says ALLOW because one paisa clears every cap; the accountant
    authors the mint, the settlement is unique and the balance is positive, so
    the auditor reports clean. Nothing here is wrong except the number the
    whole calculation rests on, which the party being paid chose.
    """
    _ask(exchange, "ord_self_ask", "m_buyer", limit_price=100_000_000)
    _ask(exchange, "ord_self_bid", "m_buyer", limit_price=100_000_000,
         side=Side.BID)

    decision, settlement = exchange.execute_match(
        Match("mch_self", "ord_self_bid", "ord_self_ask", 1, 1, 1.0, "self-dealt"),
        "m_buyer", "m_buyer", TRUSTED, correlation_id="c1",
    )

    assert decision.verdict == Verdict.ALLOW, "the exploit clears the gate"
    assert settlement.status == SettlementStatus.COMPLETED
    assert settlement.amount == 1, "one paisa of real money moved"
    assert _minted(exchange) == 0, "and nothing may be minted for it"
    assert "POINTS_MINTED" not in [e.type for e in exchange.log.read_all()]


def test_an_ask_posted_by_anyone_but_the_seller_mints_nothing(exchange):
    """A third party's ask is not this seller's asking price. The buyer would
    otherwise shop the log for the highest limit_price it could find."""
    _ask(exchange, "ord_stranger_ask", "m_stranger", limit_price=100_000_000)

    exchange.execute_match(
        Match("mch_1", "ord_bid", "ord_stranger_ask", 1, 1, 1.0, "borrowed ask"),
        "m_buyer", "m_seller", TRUSTED, correlation_id="c1",
    )

    assert _minted(exchange) == 0


def test_a_bid_named_as_the_ask_mints_nothing(exchange):
    """A bid's limit_price is a ceiling, not an asking price — and a buyer's own
    bid is posted at whatever ceiling it likes."""
    _ask(exchange, "ord_a_bid", "m_seller", limit_price=100_000_000, side=Side.BID)

    exchange.execute_match(
        Match("mch_1", "ord_bid", "ord_a_bid", 1, 1, 1.0, "a bid as an ask"),
        "m_buyer", "m_seller", TRUSTED, correlation_id="c1",
    )

    assert _minted(exchange) == 0


def test_two_colluding_merchants_cannot_mint_against_a_fantasy_ask(exchange):
    """Ownership alone does not close it: A posts the absurd ask, B settles a
    paisa against it, B mints millions and they split it.

    So the credited margin is bounded by `amount` — the money Razorpay actually
    moved, the one figure in the formula backed by an outside authority. A trade
    where one paisa moved earns like a trade where one paisa moved.
    """
    from exchange.house.points import BASE_POINTS

    _ask(exchange, "ord_fantasy", "m_seller", limit_price=100_000_000)

    decision, settlement = exchange.execute_match(
        Match("mch_1", "ord_bid", "ord_fantasy", 1, 1, 1.0, "collusion"),
        "m_buyer", "m_seller", TRUSTED, correlation_id="c1",
    )

    assert decision.verdict == Verdict.ALLOW
    assert settlement.status == SettlementStatus.COMPLETED
    assert _minted(exchange) == BASE_POINTS, (
        "a completed trade is worth something on its own, and nothing more: "
        "the claimed margin is capped at the one paisa that actually moved"
    )


def test_a_genuinely_well_negotiated_trade_still_earns(exchange):
    """The bound must not touch a real deal. 1900 against a 1940 ask over 200
    units is 380,000 paid on 388,000 asked — the margin is a fraction of the
    money that moved, so `min(margin, amount)` never binds."""
    from exchange.house.points import points_for_settlement

    _ask(exchange, "ord_real_ask", "m_seller", limit_price=1940, qty=1000)

    exchange.execute_match(
        Match("mch_1", "ord_bid", "ord_real_ask", 1900, 200, 0.9, "negotiated"),
        "m_buyer", "m_seller", TRUSTED, correlation_id="c1",
    )

    expected = points_for_settlement(380_000, ask_price=1940, qty=200, delivered=True)
    assert expected > 10, "this test needs a real margin to be worth paying"
    assert _minted(exchange) == expected


def test_the_mint_basis_is_the_most_recent_order_under_that_id(exchange):
    """Order ids collide across runs of a script against a persistent log, and
    the first match was whichever run got there first. `fold` already resolves a
    repost by overwriting the book, so the mint basis agrees with the book."""
    from exchange.house.points import points_for_settlement

    _ask(exchange, "ord_ask", "m_seller", limit_price=100_000_000, qty=1000)
    _ask(exchange, "ord_ask", "m_seller", limit_price=1940, qty=1000)

    exchange.execute_match(
        Match("mch_1", "ord_bid", "ord_ask", 1900, 200, 0.9, "negotiated"),
        "m_buyer", "m_seller", TRUSTED, correlation_id="c1",
    )

    assert _minted(exchange) == points_for_settlement(
        380_000, ask_price=1940, qty=200, delivered=True
    )
    assert exchange.state().open_orders["ord_ask"].limit_price == 1940


def test_the_exploit_leaves_the_auditor_with_nothing_to_report(exchange):
    """The point of the fix, stated as the auditor sees it: the self-dealt trade
    is a legitimate one-paisa trade that simply earns nothing. No violation is
    invented, and none is missed."""
    from exchange.house.accountant import Accountant

    _ask(exchange, "ord_self_ask", "m_buyer", limit_price=100_000_000)
    exchange.execute_match(
        Match("mch_self", "ord_bid", "ord_self_ask", 1, 1, 1.0, "self-dealt"),
        "m_buyer", "m_buyer", TRUSTED, correlation_id="c1",
    )

    assert Accountant(exchange.log, FakeRazorpay()).assert_invariants() == []
    assert exchange.state().credit_balances.get("m_buyer", 0) == 0


# --- a freeze binds whatever the registration order ------------------------


def test_freezing_an_unregistered_actor_stops_its_next_money_action(exchange):
    """`execute_match` never required a registration to trade, and the
    projection dropped an ACTOR_FROZEN for an actor it had not seen register.
    So the only actor that could not be contained was free to keep trading."""
    from exchange.house.accountant import Accountant

    Accountant(exchange.log, FakeRazorpay()).freeze("m_buyer", "unbacked completion")

    decision, settlement = exchange.execute_match(
        MATCH, "m_buyer", "m_seller", TRUSTED, correlation_id="c1",
    )

    assert decision.verdict == Verdict.DENY
    assert "frozen" in decision.reason.lower()
    assert settlement is None
    assert SETTLEMENT_INITIATED not in [e.type for e in exchange.log.read_all()]


def test_a_settlement_that_moves_no_money_is_refused_by_the_gate(exchange):
    """The gate was all ceilings and no floor.

    A settlement at zero passed every check: under the per-transaction cap,
    consuming none of the rolling cap, from an unfrozen actor. It moved
    nothing, minted BASE_POINTS, and raised the counterparty's standing —
    which lifts the trial cap. Repeat it for free points and free reputation.
    """
    from dataclasses import replace as dc_replace

    exchange.register_actor(Actor(actor_id="m_buyer", kind=ActorKind.MERCHANT))
    exchange.register_actor(Actor(actor_id="m_seller", kind=ActorKind.MERCHANT))

    decision, settlement = exchange.execute_match(
        dc_replace(MATCH, clearing_price=0), "m_buyer", "m_seller", TRUSTED,
        correlation_id="c1",
    )

    assert decision.verdict == Verdict.DENY
    assert settlement is None
    assert "must move money" in decision.reason
    types = [e.type for e in exchange.log.read_all()]
    assert "SETTLEMENT_INITIATED" not in types
    assert "POINTS_MINTED" not in types


def test_the_free_points_loop_mints_nothing(exchange):
    """Ten zero-value trades used to mint a hundred points, spend nothing,
    and leave the auditor with nothing to report."""
    from dataclasses import replace as dc_replace

    from exchange.house.accountant import Accountant
    from exchange.projections import fold

    exchange.register_actor(Actor(actor_id="m_buyer", kind=ActorKind.MERCHANT))
    exchange.register_actor(Actor(actor_id="m_seller", kind=ActorKind.MERCHANT))

    for i in range(10):
        exchange.execute_match(
            dc_replace(MATCH, match_id=f"mch_{i}", clearing_price=0),
            "m_buyer", "m_seller", TRUSTED, correlation_id="c1",
        )

    assert fold(exchange.log.read_all()).credit_balances.get("m_buyer", 0) == 0
    assert Accountant(exchange.log, FakeRazorpay()).assert_invariants() == []


# --- the rail's balance, now that the exchange supplies the lookup ----------
#
# `Exchange` binds the credits rail to its cached projection so a payout stops
# folding the whole log. The rail is still the lock, the figure is still
# derived from the log, and — these tests exist for this — the figure is still
# CURRENT. A cached balance that lagged one trade behind would be a ledger that
# lets the same points be spent twice.


def test_the_gate_allows_and_the_rail_still_refuses_what_cannot_be_funded(exchange):
    """Defence in depth. The gate bounds an exposure against a policy; the rail
    refuses a transfer the ledger cannot fund. Two different questions, and the
    gate saying ALLOW is not an answer to the second."""
    from exchange.rails.base import InsufficientCredits

    with pytest.raises(InsufficientCredits):
        exchange.execute_match(
            Match("mch_broke", "ord_bid", "ord_ask", 40, 100, 0.9, "test"),
            "m_pauper", "m_seller", TRUSTED, correlation_id="c1",
            currency=Currency.CREDITS,
        )

    types = [e.type for e in exchange.log.read_by_correlation("c1")]
    assert POLICY_DECIDED in types
    assert "SETTLEMENT_FAILED" in types
    assert CREDITS_TRANSFERRED not in types
    allow = [e for e in exchange.log.read_all() if e.type == POLICY_DECIDED]
    assert allow[0].payload["verdict"] == "ALLOW", "the gate did permit it"


def test_the_balance_the_rail_sees_includes_the_trade_that_just_settled(exchange):
    """A stale balance is a double spend. The buyer can fund one payout and not
    two; the second must be refused on the strength of the first."""
    from exchange.projections import fold
    from exchange.rails.base import InsufficientCredits

    exchange.log.append("genesis", CREDITS_TRANSFERRED,
                        {"from_actor_id": "genesis", "to_actor_id": "m_buyer",
                         "amount": 4000},
                        correlation_id="seed")

    _decision, settlement = exchange.execute_match(
        Match("mch_c1", "ord_bid", "ord_ask", 40, 100, 0.9, "test"),
        "m_buyer", "m_seller", TRUSTED, correlation_id="c1",
        currency=Currency.CREDITS,
    )
    assert settlement.status == SettlementStatus.COMPLETED

    with pytest.raises(InsufficientCredits):
        exchange.execute_match(
            Match("mch_c2", "ord_bid", "ord_ask", 40, 100, 0.9, "test"),
            "m_buyer", "m_seller", TRUSTED, correlation_id="c2",
            currency=Currency.CREDITS,
        )

    assert fold(exchange.log.read_all()).credit_balances["m_buyer"] == 0


def test_the_rail_ignores_a_balance_the_caller_asserts_about_itself(exchange):
    """There is no argument to `execute_match` that reaches the rail's balance
    check, and this is the test that says so. `PolicyContext` is the caller's
    own account of itself; the ledger is not."""
    from dataclasses import replace as dc_replace

    from exchange.rails.base import InsufficientCredits

    generous = dc_replace(TRUSTED, rolling_spend=-1_000_000)

    with pytest.raises(InsufficientCredits):
        exchange.execute_match(
            Match("mch_liar", "ord_bid", "ord_ask", 40, 100, 0.9, "test"),
            "m_liar", "m_seller", generous, correlation_id="c1",
            currency=Currency.CREDITS,
        )


# --- the retrieval index is a projection, so a run can resume ---------------
#
# The index used to be a plain list on the instance. A second `Exchange` over
# the same database found the ask in `state().assets`, found it in
# `open_orders`, and then intersected it with an empty index and returned
# nothing — SILENTLY. A resumed run read as "the market had no supply", which
# is the worst way for a bug to present, because it looks like an economics
# result. The market run these back is hours long and WILL be interrupted.


class CountingEmbedder:
    """`fake_embedder` with a tally of how many texts it was asked to embed.

    The count is the assertion, not the clock. `list_asset` used to hand
    `HybridIndex.index()` the whole accumulated catalogue on every listing,
    which re-embeds everything: 90 listings produced 4,095 embedded texts where
    linear is 90. A timing assertion would be flaky and would not say what went
    wrong; a count of embedded texts is exactly the quantity that was quadratic.
    """

    def __init__(self):
        self.texts_embedded = 0
        self.calls = 0

    def __call__(self, texts):
        self.calls += 1
        self.texts_embedded += len(texts)
        return fake_embedder(texts)


def _seed_supply(ex, asset_id="ast_mailers", order_id="ord_ask"):
    ex.register_actor(Actor(actor_id="m_seller", kind=ActorKind.MERCHANT))
    ex.list_asset(Asset(asset_id=asset_id, kind=AssetKind.GOODS,
                        title="biodegradable mailers compostable poly", spec={},
                        currency=Currency.INR, origin_actor_id="m_seller"))
    ex.post_order(Order(order_id=order_id, actor_id="m_seller", side=Side.ASK,
                        asset_ref=asset_id, asset_query=None, qty=1000,
                        limit_price=1940, currency=Currency.INR,
                        expires_at="2026-12-31T00:00:00+00:00", policy_snapshot={}),
                  correlation_id="c_seed")


def _hunt(ex, text="biodegradable compostable mailers"):
    from exchange.matching import find_candidates

    bid = Order(order_id="ord_bid", actor_id="m_buyer", side=Side.BID,
                asset_ref=None, asset_query={"text": text}, qty=200,
                limit_price=2200, currency=Currency.INR,
                expires_at="2026-12-31T00:00:00+00:00", policy_snapshot={})
    state = ex.state()
    asks = [o for o in state.open_orders.values() if o.side == Side.ASK]
    return find_candidates(bid, asks, state.assets, ex.index)


def test_a_fresh_exchange_matches_asks_listed_by_a_previous_one(tmp_path):
    """The resumed run. Same database, new process, new index — the ask that
    was listed an hour ago must still be findable, or an interrupted run comes
    back to an empty-looking market."""
    db = str(tmp_path / "resume.db")

    first = EventLog(db)
    ex1 = Exchange(first, HybridIndex(embed_fn=fake_embedder),
                   RazorpayRail(first, FakeRazorpay()), CreditRail(first))
    _seed_supply(ex1)
    assert [m.ask_order_id for m in _hunt(ex1)] == ["ord_ask"]
    first.close()

    second = EventLog(db)
    ex2 = Exchange(second, HybridIndex(embed_fn=fake_embedder),
                   RazorpayRail(second, FakeRazorpay()), CreditRail(second))
    try:
        assert [m.ask_order_id for m in _hunt(ex2)] == ["ord_ask"], (
            "a second Exchange over the same database found no supply for an "
            "ask that is in the book"
        )
    finally:
        second.close()


def test_a_fresh_exchange_rebuilds_the_index_from_the_log(tmp_path):
    """The mechanism behind the test above, asserted directly: the index is
    folded out of `state().assets`, not remembered by the process that listed."""
    db = str(tmp_path / "resume2.db")

    first = EventLog(db)
    ex1 = Exchange(first, HybridIndex(embed_fn=fake_embedder),
                   RazorpayRail(first, FakeRazorpay()), CreditRail(first))
    _seed_supply(ex1)
    ex1.list_asset(Asset(asset_id="ast_boxes", kind=AssetKind.GOODS,
                         title="corrugated kraft boxes recyclable", spec={},
                         currency=Currency.INR, origin_actor_id="m_seller"))
    first.close()

    second = EventLog(db)
    ex2 = Exchange(second, HybridIndex(embed_fn=fake_embedder),
                   RazorpayRail(second, FakeRazorpay()), CreditRail(second))
    try:
        assert ex2.index.size == 2
    finally:
        second.close()


def test_indexing_cost_is_linear_in_listings_not_quadratic(tmp_path):
    """90 listings embedded 4,095 texts where linear is 90 — n(n+1)/2. With
    real weights rather than a stub that is what 30 merchants seeding their
    inventories pays at startup, and it is genuinely quadratic if the runner
    lists during rounds. Asserted as a COUNT, never a timing."""
    log = EventLog(str(tmp_path / "linear.db"))
    embedder = CountingEmbedder()
    ex = Exchange(log, HybridIndex(embed_fn=embedder),
                  RazorpayRail(log, FakeRazorpay()), CreditRail(log))
    try:
        listings = 30
        for i in range(listings):
            ex.list_asset(Asset(
                asset_id=f"ast_{i}", kind=AssetKind.GOODS,
                title=f"biodegradable mailers batch {i}", spec={},
                currency=Currency.INR, origin_actor_id="m_seller",
            ))

        assert ex.index.size == listings
        assert embedder.texts_embedded == listings, (
            f"{listings} listings embedded {embedder.texts_embedded} texts; "
            f"linear is {listings}, quadratic is {listings * (listings + 1) // 2}"
        )
    finally:
        log.close()


def test_relisting_an_asset_does_not_duplicate_it_in_the_index(tmp_path):
    """The index is folded from `state().assets`, which is keyed by asset_id,
    so a runner that re-lists its inventory on resume is idempotent rather than
    doubling the corpus every run."""
    log = EventLog(str(tmp_path / "relist.db"))
    embedder = CountingEmbedder()
    ex = Exchange(log, HybridIndex(embed_fn=embedder),
                  RazorpayRail(log, FakeRazorpay()), CreditRail(log))
    try:
        asset = Asset(asset_id="ast_mailers", kind=AssetKind.GOODS,
                      title="biodegradable mailers compostable poly", spec={},
                      currency=Currency.INR, origin_actor_id="m_seller")
        ex.list_asset(asset)
        ex.list_asset(asset)

        assert ex.index.size == 1
        assert embedder.texts_embedded == 1
    finally:
        log.close()


# --- a rejected capture poll must not kill the run -------------------------


def test_an_allow_still_resolves_when_the_capture_poll_is_rejected(tmp_path):
    """The shape that killed the run: SETTLEMENT_INITIATED is already written
    when the poll fires, so a 429 there used to propagate out of
    `execute_match` and leave an ALLOW with no outcome and a dead process."""
    from tests.test_rails import RefusingLookup

    log = EventLog(str(tmp_path / "poll.db"))
    ex = Exchange(log, HybridIndex(embed_fn=fake_embedder),
                  RazorpayRail(log, RefusingLookup(), poll_attempts=1,
                               poll_interval=0),
                  CreditRail(log))
    try:
        decision, settlement = ex.execute_match(
            MATCH, "m_buyer", "m_seller", TRUSTED, correlation_id="c1")

        assert decision.verdict == Verdict.ALLOW
        assert settlement is not None, "the ALLOW resolved to nothing"
        assert settlement.status == SettlementStatus.PENDING
        types = [e.type for e in log.read_by_correlation("c1")]
        assert "CAPTURE_POLL_FAILED" in types
    finally:
        log.close()


# --- the rolling spend cap is a WINDOW, and the gate derives it ------------
#
# `_spend_to_date` used to sum every settlement ever initiated. `runs/*.db`
# survives every tuning re-run, so a lifetime cap of 10,00,000 paise is about
# twenty-five typical trades EVER: it binds partway through the third run and
# every trade after it is a correct DENY that reads like a broken gate.


def _append_at(log, ts, actor_id, type, payload, correlation_id):
    """Append an event stamped at a chosen time, straight through SQLite.

    The window can only be tested by a log that HOLDS an old event, and the
    log is append-only by database trigger, so an existing row cannot be
    re-dated. INSERT is permitted — only UPDATE and DELETE raise — so this
    writes the row a previous run would have written, with that run's
    timestamp. Nothing here weakens the append-only guarantee; it is the same
    operation `EventLog.append` performs, with the clock supplied.
    """
    import json

    log._conn.execute(
        "INSERT INTO events (event_id, ts, actor_id, type, payload, "
        "causation_id, correlation_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (f"evt_backdated_{ts}", ts, actor_id, type, json.dumps(payload),
         None, correlation_id),
    )
    log._conn.commit()


def _windowed_exchange(tmp_path, window_seconds, cap=250_000):
    log = EventLog(str(tmp_path / "window.db"))
    fake = FakeRazorpay()
    limits = PolicyLimits(
        per_txn_cap=200_000,
        rolling_window_cap=cap,
        human_approval_threshold=1_000_000_000,
        unknown_counterparty_cap=1_000_000_000,
        rolling_window_seconds=window_seconds,
    )
    ex = Exchange(log, HybridIndex(embed_fn=fake_embedder),
                  RazorpayRail(log, fake, poll_attempts=1, poll_interval=0),
                  CreditRail(log), inr_limits=limits)
    return log, ex


def test_spend_older_than_the_window_no_longer_binds(tmp_path):
    """Two trades that would breach a cumulative cap do not breach a windowed
    one when the first has aged out. Written straight to the log with an old
    timestamp, because that is the only thing that distinguishes the two
    designs."""
    log, ex = _windowed_exchange(tmp_path, window_seconds=3600)
    try:
        two_hours_ago = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat()
        _append_at(log, two_hours_ago, "m_buyer", SETTLEMENT_INITIATED, {
            "settlement_id": "stl_old", "match_id": "mch_old", "currency": "INR",
            "amount": 150_000, "razorpay_order_id": "order_old",
        }, correlation_id="c_old")
        # Sanity: the lifetime figure still sees it. Only the window does not.
        assert ex.state().spend_to_date["m_buyer"]["INR"] == 150_000

        decision, settlement = ex.execute_match(
            Match("mch_new", "ord_bid", "ord_ask", 300, 500, 0.9, "test"),
            "m_buyer", "m_seller", TRUSTED, correlation_id="c_new")

        assert decision.verdict == Verdict.ALLOW, decision.reason
        assert settlement is not None
        assert decision.limits_evaluated["rolling_spend"] == 0
    finally:
        log.close()


def test_spend_inside_the_window_still_binds(tmp_path):
    """The other half. Windowing must not become 'no cap at all' — a merchant
    that spends the cap within the hour is still refused."""
    log, ex = _windowed_exchange(tmp_path, window_seconds=3600)
    try:
        first, _ = ex.execute_match(
            Match("mch_1", "ord_bid", "ord_ask", 300, 500, 0.9, "test"),
            "m_buyer", "m_seller", TRUSTED, correlation_id="c1")
        assert first.verdict == Verdict.ALLOW

        second, settlement = ex.execute_match(
            Match("mch_2", "ord_bid", "ord_ask", 300, 500, 0.9, "test"),
            "m_buyer", "m_seller", TRUSTED, correlation_id="c2")

        assert second.verdict == Verdict.DENY
        assert settlement is None
        assert second.limits_evaluated["rolling_spend"] == 150_000
    finally:
        log.close()


def test_the_deny_names_the_window_it_is_measured_over(tmp_path):
    """A merchant reading a cap with no period attached cannot tell a bound
    that will lift from one that never will."""
    log, ex = _windowed_exchange(tmp_path, window_seconds=3600)
    try:
        ex.execute_match(
            Match("mch_1", "ord_bid", "ord_ask", 300, 500, 0.9, "test"),
            "m_buyer", "m_seller", TRUSTED, correlation_id="c1")
        decision, _ = ex.execute_match(
            Match("mch_2", "ord_bid", "ord_ask", 300, 500, 0.9, "test"),
            "m_buyer", "m_seller", TRUSTED, correlation_id="c2")

        assert "1h" in decision.reason
        assert decision.limits_evaluated["rolling_window_seconds"] == 3600
    finally:
        log.close()


def test_the_window_is_configuration_not_something_the_caller_can_widen(tmp_path):
    """A cap the actor supplies its own usage figure for is not a cap, and the
    same is true of the period that figure is measured over. `execute_match`
    takes no window; it reads the exchange's own limits."""
    import inspect

    signature = inspect.signature(Exchange.execute_match)
    assert "window" not in " ".join(signature.parameters)
    assert "limits" not in signature.parameters

    log, ex = _windowed_exchange(tmp_path, window_seconds=3600)
    try:
        ex.execute_match(
            Match("mch_1", "ord_bid", "ord_ask", 300, 500, 0.9, "test"),
            "m_buyer", "m_seller", TRUSTED, correlation_id="c1")
        # A context claiming no spend at all changes nothing: the figure is
        # re-derived from the log either way.
        liar = PolicyContext(actor_status=ActorStatus.ACTIVE, rolling_spend=0,
                             counterparty_confidence=0.9)
        decision, _ = ex.execute_match(
            Match("mch_2", "ord_bid", "ord_ask", 300, 500, 0.9, "test"),
            "m_buyer", "m_seller", liar, correlation_id="c2")

        assert decision.verdict == Verdict.DENY
        assert decision.limits_evaluated["rolling_spend"] == 150_000
    finally:
        log.close()


def test_an_unreadable_timestamp_counts_against_the_cap(tmp_path):
    """Wrong in the direction of refusing, which is the only direction a cap
    may be wrong in. A timestamp nobody can parse is not evidence of headroom."""
    log, ex = _windowed_exchange(tmp_path, window_seconds=1)
    try:
        _append_at(log, "not-a-timestamp", "m_buyer", SETTLEMENT_INITIATED, {
            "settlement_id": "stl_odd", "match_id": "mch_odd", "currency": "INR",
            "amount": 150_000, "razorpay_order_id": "order_odd",
        }, correlation_id="c_odd")

        decision, _ = ex.execute_match(
            Match("mch_new", "ord_bid", "ord_ask", 300, 500, 0.9, "test"),
            "m_buyer", "m_seller", TRUSTED, correlation_id="c_new")

        assert decision.verdict == Verdict.DENY
        assert decision.limits_evaluated["rolling_spend"] == 150_000
    finally:
        log.close()
