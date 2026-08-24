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
