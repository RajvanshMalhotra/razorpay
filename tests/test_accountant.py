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
from exchange.policy import GATE_ACTOR_ID
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


def _captured(order_id="order_1", payment_id="pay_1"):
    return FakeRazorpay(payments_by_order={
        order_id: {"count": 1, "items": [{"id": payment_id, "status": "captured"}]}
    })


def test_a_settlement_captured_upstream_but_pending_locally_is_drift(log):
    """The dropped webhook. The whole failure demo rests on catching this."""
    _initiated(log, "stl_1", "order_1")

    result = Accountant(log, _captured()).reconcile()

    assert len(result.drifts) == 1
    assert result.drifts[0].local_status == "PENDING"
    assert result.drifts[0].remote_status == "captured"
    assert result.unbacked == [], "this is the repairable direction"


def test_a_settlement_pending_on_both_sides_is_not_drift(log):
    _initiated(log, "stl_1", "order_1")

    assert Accountant(log, FakeRazorpay(payments_by_order={})).reconcile().clean


def test_a_completed_settlement_with_a_captured_payment_is_not_drift(log):
    _initiated(log, "stl_1", "order_1")
    log.append("m_a", SETTLEMENT_COMPLETED,
               {"settlement_id": "stl_1", "razorpay_payment_id": "pay_1"},
               correlation_id="c")

    assert Accountant(log, _captured()).reconcile().clean


def test_reconciling_logs_what_it_checked(log):
    _initiated(log, "stl_1", "order_1")

    Accountant(log, FakeRazorpay(payments_by_order={})).reconcile()

    assert any(e.type == "RECONCILED" for e in log.read_all())


def test_drift_is_logged_with_both_sides(log):
    _initiated(log, "stl_1", "order_1")

    Accountant(log, _captured()).reconcile()

    drift = [e for e in log.read_all() if e.type == "DRIFT_DETECTED"][0]
    assert drift.payload["local_status"] == "PENDING"
    assert drift.payload["remote_status"] == "captured"


# --- the other direction: a completion the remote does not confirm ----------


def _unbacked(log, sid="stl_1", order_id="order_1", corr="c"):
    """Locally COMPLETED, and Razorpay shows nothing captured."""
    _initiated(log, sid, order_id, corr=corr)
    log.append("m_a", SETTLEMENT_COMPLETED,
               {"settlement_id": sid, "razorpay_payment_id": "pay_1"},
               correlation_id=corr)
    return FakeRazorpay(payments_by_order={})


def test_a_local_completion_the_remote_does_not_confirm_is_detected(log):
    """The direction reconcile used to compute and throw away.

    It is the expensive one: HouseAgent.observe mines completed settlements
    into insight lots and the memory loop reads a clean settlement as a
    delivery signal, so an unbacked completion becomes evidence of reliability,
    is sold on as market intelligence, and raises the trial cap for a merchant
    that was never paid.
    """
    client = _unbacked(log)

    result = Accountant(log, client).reconcile()

    assert result.drifts == [], "this is not the repairable direction"
    assert len(result.unbacked) == 1
    assert result.unbacked[0].settlement_id == "stl_1"
    assert result.unbacked[0].local_status == "COMPLETED"
    assert result.unbacked[0].remote_status == "none"
    assert result.unbacked[0].actor_id == "m_a"


def test_the_two_drift_directions_are_distinguishable_in_the_log(log):
    """Different event types, not one type with a field to squint at.

    They demand opposite responses — one is repaired, the other can only be
    contained — so a reader must be able to tell them apart at a glance.
    """
    _initiated(log, "stl_pending", "order_pending", corr="c_pending")
    _unbacked(log, sid="stl_done", order_id="order_done", corr="c_done")
    client = FakeRazorpay(payments_by_order={
        "order_pending": {"count": 1,
                          "items": [{"id": "pay_p", "status": "captured"}]},
        "order_done": {"count": 0, "items": []},
    })

    Accountant(log, client).reconcile()

    forward = [e for e in log.read_all() if e.type == "DRIFT_DETECTED"]
    reverse = [e for e in log.read_all()
               if e.type == "UNBACKED_COMPLETION_DETECTED"]
    assert [e.payload["settlement_id"] for e in forward] == ["stl_pending"]
    assert [e.payload["settlement_id"] for e in reverse] == ["stl_done"]


def test_an_unbacked_completion_is_filed_on_the_trades_own_thread(log):
    """Same reasoning as DRIFT_DETECTED: the contradiction belongs where the
    completion it contradicts is, not in a reconciliation index."""
    client = _unbacked(log, corr="c_trade")

    Accountant(log, client).reconcile()

    story = [e.type for e in log.read_by_correlation("c_trade")]
    assert story == ["SETTLEMENT_INITIATED", "SETTLEMENT_COMPLETED",
                     "UNBACKED_COMPLETION_DETECTED", "ACTOR_FROZEN"]


def test_an_unbacked_completion_freezes_the_actor_it_credits(log):
    """Not a caller's judgment call, unlike a repairable drift. Nothing here
    can make these books honest again, so the containment cannot be optional —
    and the record is already feeding the insight miner and the trial cap."""
    from exchange.models import ActorStatus
    from exchange.projections import fold

    log.append("m_a", "ACTOR_REGISTERED",
               {"actor_id": "m_a", "kind": "MERCHANT"}, correlation_id="reg")
    client = _unbacked(log)

    Accountant(log, client).reconcile()

    assert fold(log.read_all()).actors["m_a"].status == ActorStatus.FROZEN


def test_an_unbacked_completion_is_an_invariant_violation(log):
    """A freeze is meant to be lifted; a completion nobody paid for is not.
    The auditor keeps saying so on every run."""
    client = _unbacked(log)
    accountant = Accountant(log, client)
    accountant.reconcile()

    violations = accountant.assert_invariants()

    assert any(v.kind == "unbacked_completion" and "stl_1" in v.detail
               for v in violations)


def test_repair_refuses_an_unbacked_completion(log):
    """It is not silently repaired, and it is not repaired at all.

    Repairing would mean appending a completion the remote denies — the exact
    thing repair() already refuses when no captured payment exists.
    """
    client = _unbacked(log)
    accountant = Accountant(log, client)
    found = accountant.reconcile().unbacked[0]

    with pytest.raises(ValueError, match="not a drift"):
        accountant.repair(found)

    completions = [e for e in log.read_all() if e.type == "SETTLEMENT_COMPLETED"]
    assert len(completions) == 1, "no second completion was written"


def test_an_unbacked_completion_is_contained_once_but_reported_every_run(log):
    """The log cannot be un-appended, so the condition holds forever. Report it
    every run; do not bury the trade's thread in duplicate freezes."""
    client = _unbacked(log)
    accountant = Accountant(log, client)

    accountant.reconcile()
    second = accountant.reconcile()

    assert len(second.unbacked) == 1, "still wrong, still reported"
    types = [e.type for e in log.read_all()]
    assert types.count("UNBACKED_COMPLETION_DETECTED") == 1
    assert types.count("ACTOR_FROZEN") == 1


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
    # Authored by the gate, which is the only signature that permits anything.
    log.append(GATE_ACTOR_ID, POLICY_DECIDED,
               {"decision_id": "d1", "action_ref": "mch_1", "verdict": "ALLOW",
                "reason": "ok", "limits_evaluated": {}, "ts": "t"},
               correlation_id="c")
    _initiated(log, "stl_1", "order_1", match_id="mch_1")

    assert Accountant(log, FakeRazorpay()).assert_invariants() == []


def test_a_merchant_cannot_sign_its_own_permit(log):
    """The auditor built `allowed` from any ALLOW, whoever wrote it.

    A broker can write a POLICY_DECIDED — `Broker._log_refusal` does, so a
    refusal reads in the gate's vocabulary — so a merchant could author its
    own ALLOW and satisfy `ungated_settlement`. The mint invariant checked
    its author; this one did not. Same shape as the freeze that never bound:
    a value the checker must be authoritative about, supplied by the party
    it constrains.
    """
    log.append("m_a", MATCH_PROPOSED,
               {"match_id": "mch_1", "clearing_price": 1940, "qty": 200},
               correlation_id="c")
    log.append("m_a", POLICY_DECIDED,  # the merchant permits itself
               {"decision_id": "d1", "action_ref": "mch_1", "verdict": "ALLOW",
                "reason": "looks fine to me", "limits_evaluated": {}, "ts": "t"},
               correlation_id="c")
    _initiated(log, "stl_1", "order_1", match_id="mch_1")

    violations = Accountant(log, FakeRazorpay()).assert_invariants()
    kinds = {v.kind for v in violations}

    assert "self_signed_allow" in kinds
    # And the permit must not have worked: the settlement is still ungated.
    assert "ungated_settlement" in kinds


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


def _exchange_over(log):
    """A real Exchange over the accountant's own log.

    These two tests are named for a BEHAVIOUR — trading stops, trading
    resumes — and asserting a dataclass field instead is what let the freeze
    stay decorative through several reviews: policy.py was right,
    accountant.py was right, and nothing joined them.
    """
    from exchange.rails.credits import CreditRail
    from exchange.rails.inr import RazorpayRail
    from exchange.retrieval import HybridIndex
    from exchange.service import Exchange
    from tests.test_retrieval import fake_embedder

    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_abc", "status": "captured"}]}
    })
    return Exchange(log, HybridIndex(embed_fn=fake_embedder),
                    RazorpayRail(log, fake), CreditRail(log))


def _trade(exchange, match_id, correlation_id):
    from exchange.models import ActorStatus, Match
    from exchange.policy import PolicyContext

    return exchange.execute_match(
        Match(match_id=match_id, bid_order_id="ord_bid", ask_order_id="ord_ask",
              clearing_price=1940, qty=500, score=0.9, rationale="test"),
        "m_a", "m_b",
        # What the broker actually sends: it claims ACTIVE unconditionally.
        PolicyContext(actor_status=ActorStatus.ACTIVE, rolling_spend=0,
                      counterparty_confidence=0.9),
        correlation_id=correlation_id,
    )


def _register_pair(exchange):
    from exchange.models import Actor, ActorKind

    exchange.register_actor(Actor(actor_id="m_a", kind=ActorKind.MERCHANT))
    exchange.register_actor(Actor(actor_id="m_b", kind=ActorKind.MERCHANT))


def test_freezing_an_actor_stops_it_trading(log):
    from exchange.models import ActorStatus, Verdict
    from exchange.projections import fold

    exchange = _exchange_over(log)
    _register_pair(exchange)

    Accountant(log, FakeRazorpay()).freeze("m_a", "books disagree")

    decision, settlement = _trade(exchange, "mch_1", "c_trade")

    assert decision.verdict == Verdict.DENY
    assert "frozen" in decision.reason.lower()
    assert settlement is None
    # Nothing reached the rail: a refused gate that still settles is not a gate.
    assert not any(e.type == "SETTLEMENT_INITIATED" for e in log.read_all())
    assert fold(log.read_all()).actors["m_a"].status == ActorStatus.FROZEN


def test_repairing_a_drift_completes_the_settlement_from_the_remote_truth(log):
    _initiated(log, "stl_1", "order_1")
    accountant = Accountant(log, _captured())
    drift = accountant.reconcile().drifts[0]

    accountant.repair(drift)

    assert accountant.reconcile().clean, "the drift must be gone after repair"


def test_repair_records_the_payment_id_it_recovered(log):
    _initiated(log, "stl_1", "order_1")
    accountant = Accountant(log, _captured(payment_id="pay_recovered"))
    accountant.repair(accountant.reconcile().drifts[0])

    completed = [e for e in log.read_all() if e.type == "SETTLEMENT_COMPLETED"][0]
    assert completed.payload["razorpay_payment_id"] == "pay_recovered"


def test_repairing_twice_writes_one_completion_not_two(log):
    """Two completions for one payment is not two payments, and a reader has
    to work that out for themselves. Refused rather than silently deduped."""
    _initiated(log, "stl_1", "order_1")
    accountant = Accountant(log, _captured())
    drift = accountant.reconcile().drifts[0]
    accountant.repair(drift)

    with pytest.raises(ValueError, match="already COMPLETED"):
        accountant.repair(drift)

    types = [e.type for e in log.read_all()]
    assert types.count("SETTLEMENT_COMPLETED") == 1


def test_the_whole_failure_path_is_readable_from_the_log(log):
    """Freeze, repair, resume — the forty-five seconds of the video."""
    log.append("m_a", "ACTOR_REGISTERED",
               {"actor_id": "m_a", "kind": "MERCHANT"}, correlation_id="reg")
    _initiated(log, "stl_1", "order_1")
    accountant = Accountant(log, _captured())

    drift = accountant.reconcile().drifts[0]
    accountant.freeze("m_a", f"drift on {drift.settlement_id}")
    accountant.repair(drift)
    accountant.resume("m_a")

    types = [e.type for e in log.read_all()]
    for expected in ("DRIFT_DETECTED", "ACTOR_FROZEN",
                     "SETTLEMENT_COMPLETED", "ACTOR_RESUMED"):
        assert expected in types, expected
    assert types.index("ACTOR_FROZEN") < types.index("ACTOR_RESUMED")


def test_the_trades_own_thread_carries_the_whole_arc(log):
    """Pin the trade and the WHOLE failure is right there, not filed elsewhere.

    A judge replaying one correlation must see the entire arc: initiated ->
    drift -> frozen -> completed -> resumed. The freeze and the resume are the
    middle of the failure demo — "the accountant catches it, freezes that
    actor, repairs, resumes" — and filed under freeze_{actor_id} they were the
    two chapters missing from the story the video shows.
    """
    _initiated(log, "stl_1", "order_1")
    trade = next(e for e in log.read_all()
                 if e.type == "SETTLEMENT_INITIATED").correlation_id
    accountant = Accountant(log, _captured())

    drift = accountant.reconcile().drifts[0]
    accountant.freeze("m_a", "drift", correlation_id=drift.correlation_id)
    accountant.repair(drift)
    accountant.resume("m_a", correlation_id=drift.correlation_id)

    story = [e.type for e in log.read_by_correlation(trade)]
    assert story == ["SETTLEMENT_INITIATED", "DRIFT_DETECTED", "ACTOR_FROZEN",
                     "SETTLEMENT_COMPLETED", "ACTOR_RESUMED"]


def test_the_drift_carries_the_correlation_the_freeze_needs(log):
    """The caller can only thread the freeze onto the trade if the drift hands
    it the trade's correlation. It did not, which is half of why it never was."""
    _initiated(log, "stl_1", "order_1", corr="c_trade")

    drift = Accountant(log, _captured()).reconcile().drifts[0]

    assert drift.correlation_id == "c_trade"


def test_without_a_correlation_the_freeze_stays_actor_scoped(log):
    """An actor-level freeze legitimately spans more than one trade, so the
    default must not nail it to whichever trade happened to be last."""
    accountant = Accountant(log, FakeRazorpay())

    accountant.freeze("m_a", "manual suspension")
    accountant.resume("m_a")

    story = [e.type for e in log.read_by_correlation("freeze_m_a")]
    assert story == ["ACTOR_FROZEN", "ACTOR_RESUMED"]


def test_repair_refuses_when_the_remote_shows_no_captured_payment(log):
    """The remote is the authority. No captured payment, no completion.

    Reachable when a payment is reversed between reconcile() and repair().
    Writing SETTLEMENT_COMPLETED with a null payment id would turn the
    repair tool into a machine for asserting payments that never occurred.
    """
    _initiated(log, "stl_1", "order_1")
    drift = Accountant(log, _captured()).reconcile().drifts[0]

    gone = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_1", "status": "refunded"}]}
    })
    with pytest.raises(ValueError, match="no captured payment"):
        Accountant(log, gone).repair(drift)

    assert not any(e.type == "SETTLEMENT_COMPLETED" for e in log.read_all())


def test_a_resumed_actor_can_trade_again(log):
    """The freeze lifts, or it is a ban rather than a hold."""
    from exchange.models import ActorStatus, Verdict
    from exchange.projections import fold

    exchange = _exchange_over(log)
    _register_pair(exchange)
    accountant = Accountant(log, FakeRazorpay())

    accountant.freeze("m_a", "books disagree")
    assert _trade(exchange, "mch_1", "c_trade")[0].verdict == Verdict.DENY

    accountant.resume("m_a")

    # A fresh match_id: the denied one is spent, and a retry at different terms
    # is a new match rather than a second decision under one action_ref.
    decision, _ = _trade(exchange, "mch_2", "c_trade")

    assert decision.verdict == Verdict.ALLOW
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
