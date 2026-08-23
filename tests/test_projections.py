import pytest

from exchange.events import (
    ACTOR_REGISTERED,
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
from exchange.projections import fold


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


def test_folding_a_partial_slice_raises_rather_than_inventing_state():
    """Pins fold's precondition: the whole log from seq 1, never a slice.

    read_since(seq) hands back exactly this shape — a completion whose
    initiation fell outside the window — and there is no correct state to
    produce from it, so it must fail loudly rather than fabricate a record.
    """
    with pytest.raises(KeyError):
        fold([
            _ev(7, SETTLEMENT_COMPLETED, {
                "settlement_id": "stl_1",
                "razorpay_payment_id": "pay_xyz",
            }),
        ])


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
