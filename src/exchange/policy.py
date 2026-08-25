"""The policy gate.

Every money action is evaluated here first, and the decision is logged whether
the answer is yes or no. Explainability is the point: a decision always carries
the reason and the limits it weighed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from exchange.ids import new_id
from exchange.models import ActorStatus, Currency, PolicyDecision, Verdict

# The gate signs its own decisions, and lives here rather than in `service`
# so the accountant can check the signature without importing the thing it
# audits. A merchant can write a POLICY_DECIDED — `Broker._log_refusal` does,
# so a refusal reads in the gate's vocabulary — and an ALLOW is only worth
# anything if you can tell who wrote it.
GATE_ACTOR_ID = "gate"


@dataclass(frozen=True)
class PolicyLimits:
    """The caps. Configuration of the exchange, never an argument of a trade.

    `rolling_window_seconds` is the window `rolling_window_cap` is a cap OVER.
    It lives here, beside the figure it qualifies, because a cap without its
    period is not a cap — it was read as "for life" for exactly as long as the
    field was missing, and a lifetime cap on a persistent log is a cap that
    runs out and never comes back.
    """

    per_txn_cap: int
    rolling_window_cap: int
    human_approval_threshold: int
    unknown_counterparty_cap: int
    confidence_floor: float = 0.3
    rolling_window_seconds: int = 3600


@dataclass(frozen=True)
class PolicyContext:
    """The facts the gate weighs about the actor making the request.

    `rolling_spend` and `actor_status` are both overwritten by
    `Exchange.execute_match` with figures derived from the event log — whatever
    a caller supplies for either one there is ignored. A cap the actor supplies
    its own usage figure for is not a cap, and a status the actor asserts about
    itself is not a status. They stay on the context because `evaluate` is also
    called directly (in tests, and by anything gating an action the exchange
    has not logged yet); on that path the caller is responsible for deriving
    them from the log itself.

    `counterparty_confidence` is genuinely the caller's to supply: it is the
    caller's own opinion of someone else, not a claim about itself.
    """

    actor_status: ActorStatus
    rolling_spend: int
    counterparty_confidence: float


# Rupee amounts in paise.
#
# ONE HOUR for the rolling window, and the arithmetic that picks it: a typical
# trade is ~388,000 paise (1,940 x 200) and a merchant makes roughly ten of
# them in a 1-2 hour run, so ~3.9 lakh paise an hour against a ceiling of 10
# lakh — comfortable headroom in a healthy run, and a broker that starts
# looping still hits the cap within the hour. It is also short enough that a
# tuning re-run started straight after the last one inherits at most one hour
# of history, which then decays, rather than inheriting everything forever.
DEFAULT_INR_LIMITS = PolicyLimits(
    per_txn_cap=2_000_000,            # Rs 20,000
    rolling_window_cap=10_000_000,    # Rs 1,00,000
    human_approval_threshold=1_500_000,  # Rs 15,000
    unknown_counterparty_cap=500_000,    # Rs 5,000 — the trial-size bound
    rolling_window_seconds=3600,
)

# Points. Deliberately looser: this rail cannot cause real harm.
DEFAULT_CREDIT_LIMITS = PolicyLimits(
    per_txn_cap=50_000,
    rolling_window_cap=200_000,
    human_approval_threshold=1_000_000_000,  # effectively never
    unknown_counterparty_cap=50_000,
    rolling_window_seconds=3600,
)


def _window_phrase(seconds: int) -> str:
    """The window as a reader of the audit trail would say it."""
    if seconds % 86_400 == 0:
        return f"{seconds // 86_400}d"
    if seconds % 3_600 == 0:
        return f"{seconds // 3_600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def evaluate(
    action_ref: str,
    actor_id: str,
    amount: int,
    currency: Currency,
    ctx: PolicyContext,
    limits: PolicyLimits,
) -> PolicyDecision:
    """Decide whether a money action may proceed.

    Precedence, highest first: frozen actor, per-transaction cap, rolling
    window, unknown-counterparty cap, human-approval threshold. A DENY at any
    level beats a REQUIRE_HUMAN below it.
    """
    evaluated = {
        "amount": amount,
        "currency": str(currency),
        "per_txn_cap": limits.per_txn_cap,
        "rolling_window_cap": limits.rolling_window_cap,
        "rolling_window_seconds": limits.rolling_window_seconds,
        "rolling_spend": ctx.rolling_spend,
        "human_approval_threshold": limits.human_approval_threshold,
        "unknown_counterparty_cap": limits.unknown_counterparty_cap,
        "counterparty_confidence": ctx.counterparty_confidence,
        "confidence_floor": limits.confidence_floor,
        "actor_status": str(ctx.actor_status),
    }

    def decide(verdict: Verdict, reason: str) -> PolicyDecision:
        return PolicyDecision(
            decision_id=new_id("dec"),
            action_ref=action_ref,
            actor_id=actor_id,
            verdict=verdict,
            reason=reason,
            limits_evaluated=evaluated,
            ts=datetime.now(timezone.utc).isoformat(),
        )

    if ctx.actor_status == ActorStatus.FROZEN:
        return decide(Verdict.DENY, "Actor is frozen pending reconciliation")

    # A money action has to involve money. The gate was all ceilings and no
    # floor, so a settlement at zero passed every check: it consumed no cap,
    # moved nothing, and still counted as a completed trade — which minted
    # BASE_POINTS and raised the counterparty's standing. Repeat it and you
    # have unlimited points for no spend.
    #
    # Refused here rather than only at the minter because the mint is not the
    # only thing a settlement of nothing corrupts: it also buys reputation,
    # and reputation lifts the trial cap.
    if amount <= 0:
        return decide(
            Verdict.DENY,
            f"Amount {amount} is not a payment; a settlement must move money",
        )

    if amount > limits.per_txn_cap:
        return decide(
            Verdict.DENY,
            f"Amount {amount} exceeds per-transaction cap {limits.per_txn_cap}",
        )

    if ctx.rolling_spend + amount > limits.rolling_window_cap:
        # The window is named in the reason, not just weighed in the decision.
        # A merchant reading "would breach rolling window cap 10000000 (spent
        # 9800000)" with no period attached cannot tell a bound that will lift
        # from a bound that never will, and for as long as the cap was
        # cumulative the honest reading was the second one.
        return decide(
            Verdict.DENY,
            f"Amount {amount} would breach the rolling window cap "
            f"{limits.rolling_window_cap} over the last "
            f"{_window_phrase(limits.rolling_window_seconds)} "
            f"(spent {ctx.rolling_spend} in that window; it frees up as the "
            "oldest of those settlements ages out)",
        )

    if (
        ctx.counterparty_confidence < limits.confidence_floor
        and amount > limits.unknown_counterparty_cap
    ):
        return decide(
            Verdict.DENY,
            f"Amount {amount} exceeds unknown counterparty cap "
            f"{limits.unknown_counterparty_cap} at confidence "
            f"{ctx.counterparty_confidence:.2f}",
        )

    if amount >= limits.human_approval_threshold:
        return decide(
            Verdict.REQUIRE_HUMAN,
            f"Amount {amount} is at or above the human approval threshold "
            f"{limits.human_approval_threshold}",
        )

    return decide(Verdict.ALLOW, "Within all configured limits")
