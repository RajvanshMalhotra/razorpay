from exchange.events import (
    ACTOR_FROZEN,
    ACTOR_REGISTERED,
    ACTOR_RESUMED,
    ASSET_LISTED,
    CREDITS_TRANSFERRED,
    MATCH_PROPOSED,
    ORDER_EXPIRED,
    ORDER_FILLED,
    ORDER_POSTED,
    SETTLEMENT_COMPLETED,
    SETTLEMENT_FAILED,
    SETTLEMENT_INITIATED,
    Event,
)
from exchange.models import ActorStatus, Currency, SettlementStatus, Side
from exchange.projections import fold, fold_from


def _ev(seq, type, payload, actor_id="m_a", correlation_id="c"):
    return Event(
        event_id=f"evt_{seq}",
        seq=seq,
        ts="2026-08-22T00:00:00+00:00",
        actor_id=actor_id,
        type=type,
        payload=payload,
        causation_id=None,
        correlation_id=correlation_id,
    )


ACTOR_PAYLOAD = {
    "actor_id": "m_a",
    "kind": "MERCHANT",
    "merchant_id": "acc_1",
    "plan_tier": "standard",
    "status": "ACTIVE",
}

ORDER_PAYLOAD = {
    "order_id": "ord_1",
    "actor_id": "m_a",
    "side": "BID",
    "asset_ref": None,
    "asset_query": {"text": "eco packaging"},
    "qty": 500,
    "limit_price": 1100000,
    "currency": "INR",
    "expires_at": "2026-09-01T00:00:00+00:00",
    "policy_snapshot": {},
}


def test_fold_of_empty_log_is_empty():
    state = fold([])

    assert state.actors == {}
    assert state.open_orders == {}
    assert state.credit_balances == {}


def test_actor_registered_appears_in_state():
    state = fold([_ev(1, ACTOR_REGISTERED, ACTOR_PAYLOAD)])

    assert state.actors["m_a"].status == ActorStatus.ACTIVE


def test_order_posted_enters_the_book():
    state = fold([_ev(1, ORDER_POSTED, ORDER_PAYLOAD)])

    order = state.open_orders["ord_1"]
    assert order.side == Side.BID
    assert order.qty == 500
    assert order.is_descriptive is True


def test_order_expired_leaves_the_book():
    state = fold([
        _ev(1, ORDER_POSTED, ORDER_PAYLOAD),
        _ev(2, ORDER_EXPIRED, {"order_id": "ord_1"}),
    ])

    assert "ord_1" not in state.open_orders


def test_partial_fill_leaves_the_order_open_with_less_quantity():
    state = fold([
        _ev(1, ORDER_POSTED, ORDER_PAYLOAD),
        _ev(2, ORDER_FILLED, {"order_id": "ord_1", "qty": 200}),
    ])

    assert state.open_orders["ord_1"].qty == 300


def test_a_full_fill_removes_the_order_from_the_book():
    """Otherwise a broker re-matches the same ask against spent inventory."""
    state = fold([
        _ev(1, ORDER_POSTED, ORDER_PAYLOAD),
        _ev(2, ORDER_FILLED, {"order_id": "ord_1", "qty": 500}),
    ])

    assert "ord_1" not in state.open_orders


def test_an_overfill_removes_the_order_rather_than_going_negative():
    state = fold([
        _ev(1, ORDER_POSTED, ORDER_PAYLOAD),
        _ev(2, ORDER_FILLED, {"order_id": "ord_1", "qty": 900}),
    ])

    assert "ord_1" not in state.open_orders


def test_a_fill_for_an_unknown_order_is_ignored():
    state = fold([
        _ev(1, ORDER_POSTED, ORDER_PAYLOAD),
        _ev(2, ORDER_FILLED, {"order_id": "ord_nonexistent", "qty": 10}),
    ])

    assert state.open_orders["ord_1"].qty == 500


def test_asset_listed_appears_in_state():
    state = fold([
        _ev(1, ASSET_LISTED, {
            "asset_id": "ast_1",
            "kind": "GOODS",
            "title": "Corrugated boxes",
            "spec": {"material": "kraft"},
            "currency": "INR",
            "origin_actor_id": "m_b",
        })
    ])

    assert state.assets["ast_1"].title == "Corrugated boxes"


def test_credits_transferred_moves_balance_both_ways():
    state = fold([
        _ev(1, CREDITS_TRANSFERRED, {"from_actor_id": "m_a", "to_actor_id": "m_b", "amount": 1200}),
    ])

    assert state.credit_balances["m_a"] == -1200
    assert state.credit_balances["m_b"] == 1200


def test_credits_are_conserved_across_many_transfers():
    events = [
        _ev(1, CREDITS_TRANSFERRED, {"from_actor_id": "m_a", "to_actor_id": "m_b", "amount": 500}),
        _ev(2, CREDITS_TRANSFERRED, {"from_actor_id": "m_b", "to_actor_id": "m_c", "amount": 200}),
        _ev(3, CREDITS_TRANSFERRED, {"from_actor_id": "m_c", "to_actor_id": "m_a", "amount": 50}),
    ]

    state = fold(events)

    assert sum(state.credit_balances.values()) == 0


def test_settlement_transitions_from_pending_to_completed():
    state = fold([
        _ev(1, SETTLEMENT_INITIATED, {
            "settlement_id": "stl_1",
            "match_id": "mch_1",
            "currency": "INR",
            "amount": 970000,
            "razorpay_order_id": "order_abc",
        }),
        _ev(2, SETTLEMENT_COMPLETED, {
            "settlement_id": "stl_1",
            "razorpay_payment_id": "pay_xyz",
        }),
    ])

    stl = state.settlements["stl_1"]
    assert stl.status == SettlementStatus.COMPLETED
    assert stl.razorpay_payment_id == "pay_xyz"
    assert stl.currency == Currency.INR


def test_settlement_transitions_to_failed_keeping_fields_set_at_initiation():
    state = fold([
        _ev(1, SETTLEMENT_INITIATED, {
            "settlement_id": "stl_1",
            "match_id": "mch_1",
            "currency": "INR",
            "amount": 970000,
            "razorpay_order_id": "order_abc",
        }),
        _ev(2, SETTLEMENT_FAILED, {
            "settlement_id": "stl_1",
            "match_id": "mch_1",
            "reason": "RuntimeError: razorpay unreachable",
        }),
    ])

    stl = state.settlements["stl_1"]
    assert stl.status == SettlementStatus.FAILED
    assert stl.razorpay_order_id == "order_abc"
    assert stl.amount == 970000
    assert stl.match_id == "mch_1"


def test_an_orphaned_completion_does_not_brick_the_projection():
    """A completion with no initiation must not make the log unreadable.

    This used to raise KeyError. The log is append-only and enforced by SQLite
    triggers, so one malformed or duplicated SETTLEMENT_COMPLETED would have
    made fold() — and therefore every read of exchange state — raise forever on
    a database that by design cannot be mended. Nothing reachable writes one,
    and the cost if anything ever did was the whole audit trail.

    read_since(seq) hands back exactly this shape, which is why fold() must
    still not be handed a partial slice: it produces usable state, not correct
    state, and the accountant is what names the difference.
    """
    state = fold([
        _ev(7, SETTLEMENT_COMPLETED, {
            "settlement_id": "stl_1",
            "razorpay_payment_id": "pay_xyz",
        }),
    ])

    stl = state.settlements["stl_1"]
    assert stl.status == SettlementStatus.COMPLETED
    assert stl.razorpay_payment_id == "pay_xyz"
    # Unknown, not zero — the completion payload carries no amount to recover.
    assert stl.amount == 0
    assert stl.match_id == ""


def test_folding_carries_on_past_an_orphaned_completion():
    """Usable state, not just a non-crash: the rest of the log still folds."""
    state = fold([
        _ev(1, SETTLEMENT_COMPLETED, {
            "settlement_id": "stl_orphan",
            "razorpay_payment_id": "pay_xyz",
        }),
        _ev(2, ACTOR_REGISTERED, {"actor_id": "m_a", "kind": "MERCHANT"}),
        _ev(3, SETTLEMENT_INITIATED, {
            "settlement_id": "stl_2",
            "match_id": "mch_2",
            "currency": "INR",
            "amount": 970_000,
            "razorpay_order_id": "order_abc",
        }),
        _ev(4, SETTLEMENT_COMPLETED, {
            "settlement_id": "stl_2",
            "razorpay_payment_id": "pay_2",
        }),
    ])

    assert state.actors["m_a"].actor_id == "m_a"
    assert state.settlements["stl_2"].amount == 970_000
    assert state.settlements["stl_2"].status == SettlementStatus.COMPLETED
    assert state.event_offset == 4


def test_an_orphaned_completion_is_reported_by_the_accountant(tmp_path):
    """Surviving it is not the same as it being fine. The fold keeps going;
    the auditor is what says the record is unbacked by any initiation."""
    from exchange.eventlog import EventLog
    from exchange.house.accountant import Accountant
    from tests.test_rails import FakeRazorpay

    lg = EventLog(str(tmp_path / "orphan.db"))
    try:
        lg.append("m_a", SETTLEMENT_COMPLETED,
                  {"settlement_id": "stl_1", "razorpay_payment_id": "pay_xyz"},
                  correlation_id="c")

        violations = Accountant(lg, FakeRazorpay()).assert_invariants()

        assert any(v.kind == "orphaned_completion" for v in violations)
    finally:
        lg.close()


# --- a freeze binds whatever the registration order ------------------------


def test_a_freeze_for_an_actor_that_never_registered_still_projects():
    """The no-op that made `_contain_unbacked` decorative.

    `execute_match` requires no registration, so an actor with no
    ACTOR_REGISTERED could trade — and could not be frozen, because the
    projection dropped the freeze on the floor.
    """
    from exchange.models import ActorKind

    state = fold([_ev(1, ACTOR_FROZEN, {"actor_id": "m_ghost", "reason": "unbacked"})])

    assert state.actors["m_ghost"].status == ActorStatus.FROZEN
    # The freeze is a fact the log holds; the kind is not, and is not invented.
    assert state.actors["m_ghost"].kind == ActorKind.UNKNOWN


def test_the_fold_does_not_raise_on_a_freeze_it_cannot_place():
    """Non-raising on events it cannot place is a deliberate property of `fold`:
    the log is append-only, so a fold that can raise is an audit trail that can
    become permanently unreadable. Binding the freeze must not cost that."""
    state = fold([
        _ev(1, ACTOR_RESUMED, {"actor_id": "m_nobody"}),
        _ev(2, ACTOR_FROZEN, {"actor_id": "m_ghost", "reason": "unbacked"}),
        _ev(3, ORDER_POSTED, ORDER_PAYLOAD),
    ])

    assert "m_nobody" not in state.actors, "a resume alone conjures no actor"
    assert state.actors["m_ghost"].status == ActorStatus.FROZEN
    assert state.open_orders["ord_1"].qty == 500, "the rest of the log still folds"


def test_registering_after_a_freeze_fills_in_the_kind_and_keeps_the_freeze():
    """Only a resume lifts a freeze. If registering could, the contained party
    would hold the fact that ends its own containment."""
    from exchange.models import ActorKind

    state = fold([
        _ev(1, ACTOR_FROZEN, {"actor_id": "m_a", "reason": "unbacked"}),
        _ev(2, ACTOR_REGISTERED, ACTOR_PAYLOAD),
    ])

    assert state.actors["m_a"].status == ActorStatus.FROZEN
    assert state.actors["m_a"].kind == ActorKind.MERCHANT
    assert state.actors["m_a"].merchant_id == "acc_1"


def test_a_resume_lifts_a_freeze_that_preceded_any_registration():
    state = fold([
        _ev(1, ACTOR_FROZEN, {"actor_id": "m_ghost", "reason": "unbacked"}),
        _ev(2, ACTOR_RESUMED, {"actor_id": "m_ghost"}),
    ])

    assert state.actors["m_ghost"].status == ActorStatus.ACTIVE


def test_match_proposed_lands_in_state_with_its_rationale():
    """The rationale is what makes a trade explainable; it must survive the fold."""
    state = fold([
        _ev(1, MATCH_PROPOSED, {
            "match_id": "mch_1",
            "bid_order_id": "ord_bid",
            "ask_order_id": "ord_ask",
            "clearing_price": 1940,
            "qty": 500,
            "score": 0.87,
            "rationale": "ast_2 ranked 0.0328 for 'compostable mailers'; priced 1940",
        }),
    ])

    match = state.matches["mch_1"]
    assert match.clearing_price == 1940
    assert match.rationale == (
        "ast_2 ranked 0.0328 for 'compostable mailers'; priced 1940"
    )
    assert match.bid_order_id == "ord_bid"
    assert match.ask_order_id == "ord_ask"


def test_fold_is_deterministic_for_the_same_events():
    events = [_ev(1, ACTOR_REGISTERED, ACTOR_PAYLOAD), _ev(2, ORDER_POSTED, ORDER_PAYLOAD)]

    assert fold(events) == fold(events)


def test_fold_records_the_offset_it_is_correct_through():
    state = fold([_ev(1, ORDER_POSTED, ORDER_PAYLOAD), _ev(2, ORDER_EXPIRED, {"order_id": "ord_1"})])

    assert state.event_offset == 2


def test_fold_from_applies_only_the_new_events():
    base = fold([_ev(1, ACTOR_REGISTERED, ACTOR_PAYLOAD)])

    grown = fold_from(base, [_ev(2, ORDER_POSTED, ORDER_PAYLOAD)])

    assert "ord_1" in grown.open_orders
    assert grown.event_offset == 2
    assert "m_a" in grown.actors  # inherited, not recomputed


def test_fold_from_equals_a_full_fold():
    """The incremental path must never disagree with the authority."""
    events = [
        _ev(1, ACTOR_REGISTERED, ACTOR_PAYLOAD),
        _ev(2, ORDER_POSTED, ORDER_PAYLOAD),
        _ev(3, CREDITS_TRANSFERRED, {"from_actor_id": "m_a", "to_actor_id": "m_b", "amount": 500}),
    ]

    incremental = fold_from(fold(events[:1]), events[1:])

    assert incremental == fold(events)


def test_fold_from_with_no_new_events_is_unchanged():
    base = fold([_ev(1, ACTOR_REGISTERED, ACTOR_PAYLOAD)])

    assert fold_from(base, []) == base
