import pytest

from exchange.eventlog import EventLog
from exchange.events import (
    CREDITS_TRANSFERRED,
    MATCH_PROPOSED,
    POLICY_DECIDED,
    SETTLEMENT_COMPLETED,
    SETTLEMENT_INITIATED,
)
from exchange.house.accountant import Accountant
from tests.test_rails import FakeRazorpay


@pytest.fixture
def log(tmp_path):
    lg = EventLog(str(tmp_path / "acct.db"))
    yield lg
    lg.close()


def _initiated(log, sid, order_id, corr="c", match_id="mch"):
    log.append("m_a", SETTLEMENT_INITIATED,
               {"settlement_id": sid, "match_id": match_id, "currency": "INR",
                "amount": 970_000, "razorpay_order_id": order_id},
               correlation_id=corr)


def test_a_settlement_captured_upstream_but_pending_locally_is_drift(log):
    """The dropped webhook. The whole failure demo rests on catching this."""
    _initiated(log, "stl_1", "order_1")
    client = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_1", "status": "captured"}]}
    })

    drifts = Accountant(log, client).reconcile()

    assert len(drifts) == 1
    assert drifts[0].local_status == "PENDING"
    assert drifts[0].remote_status == "captured"


def test_a_settlement_pending_on_both_sides_is_not_drift(log):
    _initiated(log, "stl_1", "order_1")

    assert Accountant(log, FakeRazorpay(payments_by_order={})).reconcile() == []


def test_a_completed_settlement_with_a_captured_payment_is_not_drift(log):
    _initiated(log, "stl_1", "order_1")
    log.append("m_a", SETTLEMENT_COMPLETED,
               {"settlement_id": "stl_1", "razorpay_payment_id": "pay_1"},
               correlation_id="c")
    client = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_1", "status": "captured"}]}
    })

    assert Accountant(log, client).reconcile() == []


def test_reconciling_logs_what_it_checked(log):
    _initiated(log, "stl_1", "order_1")

    Accountant(log, FakeRazorpay(payments_by_order={})).reconcile()

    assert any(e.type == "RECONCILED" for e in log.read_all())


def test_drift_is_logged_with_both_sides(log):
    _initiated(log, "stl_1", "order_1")
    client = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_1", "status": "captured"}]}
    })

    Accountant(log, client).reconcile()

    drift = [e for e in log.read_all() if e.type == "DRIFT_DETECTED"][0]
    assert drift.payload["local_status"] == "PENDING"
    assert drift.payload["remote_status"] == "captured"


def test_points_that_appear_from_nowhere_are_a_violation(log):
    """Only the accountant mints. A transfer from an actor that never
    received any is points conjured out of nothing."""
    log.append("m_a", CREDITS_TRANSFERRED,
               {"from_actor_id": "m_a", "to_actor_id": "m_b", "amount": 500},
               correlation_id="c")

    violations = Accountant(log, FakeRazorpay()).assert_invariants()

    assert any(v.kind == "points_not_conserved" for v in violations)


def test_a_settlement_without_a_preceding_allow_is_a_violation(log):
    _initiated(log, "stl_1", "order_1")

    violations = Accountant(log, FakeRazorpay()).assert_invariants()

    assert any(v.kind == "ungated_settlement" for v in violations)


def test_a_denied_match_is_not_an_orphan(log):
    """MATCH_PROPOSED precedes the gate by design, so a denied match is in
    the log legitimately. Joining on presence would flag every refusal."""
    log.append("m_a", MATCH_PROPOSED,
               {"match_id": "mch_1", "clearing_price": 1940, "qty": 200},
               correlation_id="c")
    log.append("m_a", POLICY_DECIDED,
               {"decision_id": "d1", "action_ref": "mch_1", "verdict": "DENY",
                "reason": "capped", "limits_evaluated": {}, "ts": "t"},
               correlation_id="c")

    violations = Accountant(log, FakeRazorpay()).assert_invariants()

    assert not any(v.kind == "orphaned_match" for v in violations)


def test_a_match_that_never_reached_the_gate_is_an_orphan(log):
    log.append("m_a", MATCH_PROPOSED,
               {"match_id": "mch_1", "clearing_price": 1940, "qty": 200},
               correlation_id="c")

    violations = Accountant(log, FakeRazorpay()).assert_invariants()

    assert any(v.kind == "orphaned_match" for v in violations)


def test_a_stale_projection_cache_is_a_violation(log):
    """The incremental projection's correctness rests on this check existing.
    Without it, a cache that silently lagged the log would never be caught."""
    log.append("m_a", MATCH_PROPOSED,
               {"match_id": "mch_1", "bid_order_id": "o_bid", "ask_order_id": "o_ask",
                "clearing_price": 1940, "qty": 200, "score": 1.0, "rationale": "ok"},
               correlation_id="c")

    class StaleExchange:
        def state(self):
            from exchange.projections import ExchangeState
            return ExchangeState()  # empty: pretends the log is empty

    violations = Accountant(log, FakeRazorpay(),
                            exchange=StaleExchange()).assert_invariants()

    assert any(v.kind == "projection_drift" for v in violations)


def test_a_matching_projection_is_not_a_violation(log):
    log.append("m_a", MATCH_PROPOSED,
               {"match_id": "mch_1", "bid_order_id": "o_bid", "ask_order_id": "o_ask",
                "clearing_price": 1940, "qty": 200, "score": 1.0, "rationale": "ok"},
               correlation_id="c")

    class FreshExchange:
        def __init__(self, lg):
            self._lg = lg

        def state(self):
            from exchange.projections import fold
            return fold(self._lg.read_all())

    violations = Accountant(log, FakeRazorpay(),
                            exchange=FreshExchange(log)).assert_invariants()

    assert not any(v.kind == "projection_drift" for v in violations)


def test_a_clean_log_has_no_violations(log):
    log.append("m_a", MATCH_PROPOSED,
               {"match_id": "mch_1", "clearing_price": 1940, "qty": 200},
               correlation_id="c")
    log.append("m_a", POLICY_DECIDED,
               {"decision_id": "d1", "action_ref": "mch_1", "verdict": "ALLOW",
                "reason": "ok", "limits_evaluated": {}, "ts": "t"},
               correlation_id="c")
    _initiated(log, "stl_1", "order_1", match_id="mch_1")

    assert Accountant(log, FakeRazorpay()).assert_invariants() == []


def test_settling_a_denied_match_is_caught_even_beside_an_allowed_one(log):
    """An agent capped on a full lot and retrying smaller puts a DENY and an
    ALLOW on one correlation id. Asking 'was there an ALLOW somewhere in this
    story' would let the denied match settle."""
    log.append("m_a", MATCH_PROPOSED,
               {"match_id": "mch_big", "bid_order_id": "b", "ask_order_id": "a",
                "clearing_price": 1940, "qty": 500, "score": 0.9, "rationale": "r"},
               correlation_id="c")
    log.append("m_a", POLICY_DECIDED,
               {"decision_id": "d1", "action_ref": "mch_big", "verdict": "DENY",
                "reason": "over the trial cap", "limits_evaluated": {}, "ts": "t"},
               correlation_id="c")
    log.append("m_a", MATCH_PROPOSED,
               {"match_id": "mch_small", "bid_order_id": "b", "ask_order_id": "a",
                "clearing_price": 1940, "qty": 200, "score": 0.9, "rationale": "r"},
               correlation_id="c")
    log.append("m_a", POLICY_DECIDED,
               {"decision_id": "d2", "action_ref": "mch_small", "verdict": "ALLOW",
                "reason": "ok", "limits_evaluated": {}, "ts": "t"},
               correlation_id="c")
    _initiated(log, "stl_1", "order_1", match_id="mch_big")

    violations = Accountant(log, FakeRazorpay()).assert_invariants()

    assert any(v.kind == "ungated_settlement" for v in violations)


def test_freezing_an_actor_stops_it_trading(log):
    from exchange.models import ActorStatus
    from exchange.projections import fold

    log.append("m_a", "ACTOR_REGISTERED",
               {"actor_id": "m_a", "kind": "MERCHANT"}, correlation_id="reg")
    Accountant(log, FakeRazorpay()).freeze("m_a", "books disagree")

    assert fold(log.read_all()).actors["m_a"].status == ActorStatus.FROZEN


def test_repairing_a_drift_completes_the_settlement_from_the_remote_truth(log):
    _initiated(log, "stl_1", "order_1")
    client = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_1", "status": "captured"}]}
    })
    accountant = Accountant(log, client)
    drift = accountant.reconcile()[0]

    accountant.repair(drift)

    assert accountant.reconcile() == [], "the drift must be gone after repair"


def test_repair_records_the_payment_id_it_recovered(log):
    _initiated(log, "stl_1", "order_1")
    client = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_recovered", "status": "captured"}]}
    })
    accountant = Accountant(log, client)
    accountant.repair(accountant.reconcile()[0])

    completed = [e for e in log.read_all() if e.type == "SETTLEMENT_COMPLETED"][0]
    assert completed.payload["razorpay_payment_id"] == "pay_recovered"


def test_the_whole_failure_path_is_readable_from_the_log(log):
    """Freeze, repair, resume — the forty-five seconds of the video."""
    log.append("m_a", "ACTOR_REGISTERED",
               {"actor_id": "m_a", "kind": "MERCHANT"}, correlation_id="reg")
    _initiated(log, "stl_1", "order_1")
    client = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_1", "status": "captured"}]}
    })
    accountant = Accountant(log, client)

    drift = accountant.reconcile()[0]
    accountant.freeze("m_a", f"drift on {drift.settlement_id}")
    accountant.repair(drift)
    accountant.resume("m_a")

    types = [e.type for e in log.read_all()]
    for expected in ("DRIFT_DETECTED", "ACTOR_FROZEN",
                     "SETTLEMENT_COMPLETED", "ACTOR_RESUMED"):
        assert expected in types, expected
    assert types.index("ACTOR_FROZEN") < types.index("ACTOR_RESUMED")


def test_the_trades_own_thread_carries_the_whole_arc(log):
    """Pin the trade and the drift is right there, not filed elsewhere.

    A judge replaying one correlation must see initiated -> drift -> completed.
    Filed under a reconciliation id, the middle chapter is only findable by
    someone who already knew it existed.
    """
    _initiated(log, "stl_1", "order_1")
    trade = next(e for e in log.read_all()
                 if e.type == "SETTLEMENT_INITIATED").correlation_id
    client = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_1", "status": "captured"}]}
    })
    accountant = Accountant(log, client)
    accountant.repair(accountant.reconcile()[0])

    story = [e.type for e in log.read_by_correlation(trade)]
    assert story == ["SETTLEMENT_INITIATED", "DRIFT_DETECTED",
                     "SETTLEMENT_COMPLETED"]


def test_repair_refuses_when_the_remote_shows_no_captured_payment(log):
    """The remote is the authority. No captured payment, no completion.

    Reachable when a payment is reversed between reconcile() and repair().
    Writing SETTLEMENT_COMPLETED with a null payment id would turn the
    repair tool into a machine for asserting payments that never occurred.
    """
    _initiated(log, "stl_1", "order_1")
    captured = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_1", "status": "captured"}]}
    })
    drift = Accountant(log, captured).reconcile()[0]

    gone = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_1", "status": "refunded"}]}
    })
    with pytest.raises(ValueError, match="no captured payment"):
        Accountant(log, gone).repair(drift)

    assert not any(e.type == "SETTLEMENT_COMPLETED" for e in log.read_all())


def test_a_resumed_actor_can_trade_again(log):
    from exchange.models import ActorStatus
    from exchange.projections import fold

    log.append("m_a", "ACTOR_REGISTERED",
               {"actor_id": "m_a", "kind": "MERCHANT"}, correlation_id="reg")
    accountant = Accountant(log, FakeRazorpay())
    accountant.freeze("m_a", "books disagree")
    accountant.resume("m_a")

    assert fold(log.read_all()).actors["m_a"].status == ActorStatus.ACTIVE


# --- minting: where points come from ---------------------------------------


def test_the_accountant_mints_points_against_their_source_settlement(log):
    """POINTS_MINTED was in the vocabulary and nothing emitted it. The earning
    half of the economy is only real if something writes this event."""
    Accountant(log, FakeRazorpay()).mint("m_a", 510, "stl_1", correlation_id="c")

    minted = [e for e in log.read_all() if e.type == "POINTS_MINTED"]
    assert len(minted) == 1
    assert minted[0].actor_id == "accountant"
    assert minted[0].payload["actor_id"] == "m_a"
    assert minted[0].payload["points"] == 510
    assert minted[0].payload["source_settlement_id"] == "stl_1"


def test_minted_points_land_in_the_balance(log):
    from exchange.projections import fold

    Accountant(log, FakeRazorpay()).mint("m_a", 510, "stl_1", correlation_id="c")

    assert fold(log.read_all()).credit_balances["m_a"] == 510


def test_a_settlement_earns_points_only_once(log):
    """A retried or replayed settlement path must not double-pay."""
    accountant = Accountant(log, FakeRazorpay())
    accountant.mint("m_a", 510, "stl_1", correlation_id="c")

    with pytest.raises(ValueError, match="already been minted"):
        accountant.mint("m_a", 510, "stl_1", correlation_id="c")


def test_an_opening_grant_above_the_cap_is_refused(log):
    """The grant is the one mint not derived from a trade, so it is the one
    that has to be bounded explicitly."""
    from exchange.house.points import OPENING_GRANT_CAP

    accountant = Accountant(log, FakeRazorpay())
    accountant.mint("m_a", OPENING_GRANT_CAP, None, correlation_id="c",
                    reason="opening balance")

    with pytest.raises(ValueError, match="above the cap"):
        accountant.mint("m_b", OPENING_GRANT_CAP + 1, None, correlation_id="c",
                        reason="opening balance")


def test_minting_nothing_is_refused(log):
    with pytest.raises(ValueError, match="a mint is an increase"):
        Accountant(log, FakeRazorpay()).mint("m_a", 0, "stl_1", correlation_id="c")


def test_a_house_that_spends_more_than_it_minted_is_caught(log):
    """The conservation check used to exempt the house by name — the one actor
    that actually created points. It conjured 3,850 out of nothing and the
    auditor reported zero violations."""
    log.append("house", CREDITS_TRANSFERRED,
               {"from_actor_id": "house", "to_actor_id": "m_a", "amount": 3_850},
               correlation_id="c")

    violations = Accountant(log, FakeRazorpay()).assert_invariants()

    assert any(v.kind == "points_not_conserved" and "house" in v.detail
               for v in violations)


def test_a_house_funded_by_what_it_sold_is_not_a_violation(log):
    """The house holds a real balance: it can pay out what it took in."""
    Accountant(log, FakeRazorpay()).mint("m_a", 1_200, "stl_1", correlation_id="c")
    log.append("m_a", CREDITS_TRANSFERRED,
               {"from_actor_id": "m_a", "to_actor_id": "house", "amount": 1_200},
               correlation_id="c")
    log.append("house", CREDITS_TRANSFERRED,
               {"from_actor_id": "house", "to_actor_id": "m_b", "amount": 360},
               correlation_id="c")

    violations = Accountant(log, FakeRazorpay()).assert_invariants()

    assert not any(v.kind == "points_not_conserved" for v in violations)


def test_a_mint_by_anyone_but_the_accountant_is_a_violation(log):
    """'Minted only by the accountant' was a docstring in two files and a
    check in none."""
    log.append("house", "POINTS_MINTED",
               {"actor_id": "house", "points": 5_000,
                "source_settlement_id": None, "reason": "because"},
               correlation_id="c")

    violations = Accountant(log, FakeRazorpay()).assert_invariants()

    assert any(v.kind == "unauthorized_mint" for v in violations)


def test_two_mints_against_one_settlement_are_a_violation(log):
    """mint() refuses it; the auditor catches it even if it arrived some
    other way, because the log cannot be un-appended."""
    for _ in range(2):
        log.append("accountant", "POINTS_MINTED",
                   {"actor_id": "m_a", "points": 510,
                    "source_settlement_id": "stl_1", "reason": "earned"},
                   correlation_id="c")

    violations = Accountant(log, FakeRazorpay()).assert_invariants()

    assert any(v.kind == "duplicate_mint" for v in violations)
