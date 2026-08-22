from exchange.models import ActorStatus, Currency, Verdict
from exchange.policy import PolicyContext, PolicyLimits, evaluate

LIMITS = PolicyLimits(
    per_txn_cap=1_000_000,
    rolling_window_cap=5_000_000,
    human_approval_threshold=800_000,
    unknown_counterparty_cap=500_000,
    confidence_floor=0.3,
)

TRUSTED = PolicyContext(
    actor_status=ActorStatus.ACTIVE, rolling_spend=0, counterparty_confidence=0.9
)


def _evaluate(amount, ctx=TRUSTED, limits=LIMITS):
    return evaluate("mch_1", "m_a", amount, Currency.INR, ctx, limits)


def test_small_trade_with_a_trusted_counterparty_is_allowed():
    decision = _evaluate(100_000)

    assert decision.verdict == Verdict.ALLOW
    assert decision.actor_id == "m_a"
    assert decision.action_ref == "mch_1"


def test_decision_always_records_the_limits_it_evaluated():
    decision = _evaluate(100_000)

    assert decision.limits_evaluated["per_txn_cap"] == 1_000_000
    assert decision.limits_evaluated["amount"] == 100_000


def test_frozen_actor_is_denied():
    ctx = PolicyContext(
        actor_status=ActorStatus.FROZEN, rolling_spend=0, counterparty_confidence=0.9
    )

    decision = _evaluate(1_000, ctx)

    assert decision.verdict == Verdict.DENY
    assert "frozen" in decision.reason.lower()


def test_amount_over_per_txn_cap_is_denied():
    decision = _evaluate(1_000_001)

    assert decision.verdict == Verdict.DENY
    assert "per-transaction" in decision.reason


def test_amount_that_breaches_rolling_window_is_denied():
    ctx = PolicyContext(
        actor_status=ActorStatus.ACTIVE,
        rolling_spend=4_900_000,
        counterparty_confidence=0.9,
    )

    decision = _evaluate(200_000, ctx)

    assert decision.verdict == Verdict.DENY
    assert "rolling" in decision.reason


def test_unknown_counterparty_is_capped_low():
    unknown = PolicyContext(
        actor_status=ActorStatus.ACTIVE, rolling_spend=0, counterparty_confidence=0.05
    )

    decision = _evaluate(600_000, unknown)

    assert decision.verdict == Verdict.DENY
    assert "unknown counterparty" in decision.reason


def test_unknown_counterparty_may_still_trade_small():
    unknown = PolicyContext(
        actor_status=ActorStatus.ACTIVE, rolling_spend=0, counterparty_confidence=0.05
    )

    decision = _evaluate(400_000, unknown)

    assert decision.verdict == Verdict.ALLOW


def test_large_trade_requires_human_approval():
    decision = _evaluate(900_000)

    assert decision.verdict == Verdict.REQUIRE_HUMAN
    assert "human" in decision.reason.lower()


def test_deny_beats_require_human():
    """Over both the human threshold and the per-txn cap: DENY wins."""
    decision = _evaluate(2_000_000)

    assert decision.verdict == Verdict.DENY


def test_decision_ids_are_unique():
    a = _evaluate(1_000)
    b = _evaluate(1_000)

    assert a.decision_id != b.decision_id
