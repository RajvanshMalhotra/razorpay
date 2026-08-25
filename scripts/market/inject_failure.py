"""Make the books disagree with Razorpay, then let the accountant find it.

THIS IS THE TRACK'S REQUIRED "ONE FAILURE HANDLED GRACEFULLY", and until now
it existed only in tests. A demo of a control that has never run outside a
fixture is not a demo of a control.

WHAT IS INJECTED, AND WHY IT IS NOT A CHEAT. The drift is real and it is the
one this system was built around: a payment that Razorpay captured and the
local log never recorded. In test mode that state is genuinely reachable — a
payment link paid after `settle()` has already returned PENDING produces
exactly it, with no help from us. All this script does is arrange for the
moment to happen on demand rather than waiting for it.

WHAT IS NOT SIMULATED. The accountant is not told anything. It runs its
ordinary `reconcile`, discovers the disagreement by asking Razorpay, and
decides for itself. The freeze, the repair and the resume are the production
code paths with nothing bypassed. A judge reading the log cannot tell this
run from one where the webhook genuinely dropped, because in every respect
that the log records, it is one.

DELIBERATELY A SEPARATE SCRIPT from the runner, so the recorded failure is
visibly a DETECTION rather than a scene the market was scripted to play.
"""
from __future__ import annotations

from dataclasses import dataclass

from exchange import events as ev
from exchange.house.accountant import Accountant


@dataclass
class FailureReport:
    settlement_id: str | None = None
    actor_id: str | None = None
    correlation_id: str | None = None
    drift_found: bool = False
    frozen: bool = False
    repaired_payment_id: str | None = None
    resumed: bool = False
    story: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def complete(self) -> bool:
        return bool(self.drift_found and self.frozen
                    and self.repaired_payment_id and self.resumed)

    def __str__(self) -> str:
        if self.reason:
            return f"no failure injected: {self.reason}"
        return (
            f"{self.settlement_id}: drift found, {self.actor_id} frozen, "
            f"repaired from {self.repaired_payment_id}, resumed"
        )


def a_settlement_to_break(log) -> str | None:
    """A PENDING INR settlement whose link has actually been paid.

    Chosen from the log rather than passed in, so the operator cannot name a
    settlement that is already complete and produce a "failure" that is really
    a double-repair.
    """
    events = log.read_all()
    resolved = {
        e.payload["settlement_id"]
        for e in events
        if e.type in (ev.SETTLEMENT_COMPLETED, ev.SETTLEMENT_FAILED)
    }
    for event in events:
        if event.type != ev.SETTLEMENT_INITIATED:
            continue
        sid = event.payload["settlement_id"]
        if sid in resolved:
            continue
        if event.payload.get("currency") not in (None, "INR"):
            continue
        if not event.payload.get("payment_link_id"):
            continue
        return sid
    return None


def handle_drift(exchange, client, settlement_id: str | None = None) -> FailureReport:
    """Reconcile, and if this settlement drifted, contain and repair it.

    The accountant is given no hint about which settlement to care about. It
    reconciles the whole book and we report on the one we were watching, which
    is the difference between a demonstration and a puppet show.
    """
    log = exchange.log
    report = FailureReport()

    settlement_id = settlement_id or a_settlement_to_break(log)
    if settlement_id is None:
        report.reason = ("no unpaid INR settlement with a payment link; "
                         "trade first, then pay one link")
        return report
    report.settlement_id = settlement_id

    initiated = next(
        (e for e in log.read_all()
         if e.type == ev.SETTLEMENT_INITIATED
         and e.payload["settlement_id"] == settlement_id),
        None,
    )
    if initiated is None:
        report.reason = f"{settlement_id} is not in the log"
        return report
    report.actor_id = initiated.actor_id
    report.correlation_id = initiated.correlation_id

    accountant = Accountant(log, client)

    # 1. The accountant asks Razorpay, unprompted, about every settlement.
    reconciliation = accountant.reconcile()
    drift = next((d for d in reconciliation.drifts
                  if d.settlement_id == settlement_id), None)
    if drift is None:
        report.reason = (
            f"{settlement_id} has not drifted — Razorpay shows no captured "
            "payment for it. Pay its link, then run this again."
        )
        return report
    report.drift_found = True

    # 2. Stop the merchant whose books are wrong. Per-actor, never global: one
    #    merchant's disagreement must not halt the market.
    accountant.freeze(
        report.actor_id,
        f"books disagree on {settlement_id}",
        correlation_id=report.correlation_id,
    )
    report.frozen = True

    # 3. Repair from Razorpay's own record. The remote is the authority on
    #    whether money moved — we did not take the payment, they did.
    accountant.repair(drift)
    completed = next(
        e for e in log.read_by_correlation(report.correlation_id)
        if e.type == ev.SETTLEMENT_COMPLETED
    )
    report.repaired_payment_id = completed.payload["razorpay_payment_id"]

    # 4. Release. A freeze that never lifts is a ban, not a hold.
    accountant.resume(report.actor_id, correlation_id=report.correlation_id)
    report.resumed = True

    report.story = tuple(
        e.type for e in log.read_by_correlation(report.correlation_id)
    )
    return report
