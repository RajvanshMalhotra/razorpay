"""The books, and whether they are honest.

Exchange-level rather than per-merchant: reconciliation needs both sides of
every trade, and point conservation is a global invariant that a per-merchant
accountant would only ever see half of.

Its reconciliation against Razorpay is also the DELIVERY SIGNAL the memory
loop has been missing — a settlement that completes cleanly is evidence of
reliability, one that drifts is evidence against.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.insights import HOUSE_ACTOR_ID

ACCOUNTANT_ACTOR_ID = "accountant"


@dataclass(frozen=True)
class Drift:
    settlement_id: str
    local_status: str
    remote_status: str


@dataclass(frozen=True)
class Violation:
    kind: str
    detail: str


class Accountant:
    def __init__(self, log: EventLog, client, exchange=None) -> None:
        self._log = log
        self._client = client
        # Optional: when given, the accountant checks that the Exchange's
        # incremental projection still agrees with a full fold. That check is
        # the only thing standing between a fast cache and a second source of
        # truth, so the cache is only safe BECAUSE this exists.
        self._exchange = exchange

    def reconcile(self) -> list[Drift]:
        """Compare local settlement records against Razorpay's own state.

        Catches the dropped webhook: captured upstream, still PENDING here.
        That mismatch is what the failure demo turns on, and it is a far
        better thing to show than a declined card.
        """
        events = self._log.read_all()
        completed = {
            e.payload["settlement_id"]
            for e in events if e.type == ev.SETTLEMENT_COMPLETED
        }

        drifts: list[Drift] = []
        checked = 0
        for event in events:
            if event.type != ev.SETTLEMENT_INITIATED:
                continue
            order_id = event.payload.get("razorpay_order_id")
            if not order_id:
                continue
            checked += 1
            sid = event.payload["settlement_id"]
            local = "COMPLETED" if sid in completed else "PENDING"

            payments = self._client.order.payments(order_id)
            remote = "none"
            for item in payments.get("items", []):
                if item.get("status") == "captured":
                    remote = "captured"
                    break

            if local == "PENDING" and remote == "captured":
                drift = Drift(sid, local, remote)
                drifts.append(drift)
                self._log.append(
                    ACCOUNTANT_ACTOR_ID, ev.DRIFT_DETECTED,
                    {"settlement_id": sid, "local_status": local,
                     "remote_status": remote, "razorpay_order_id": order_id},
                    correlation_id=f"recon_{sid}",
                )

        self._log.append(
            ACCOUNTANT_ACTOR_ID, ev.RECONCILED,
            {"settlements_checked": checked, "drifts": len(drifts)},
            correlation_id="recon",
        )
        return drifts

    def assert_invariants(self) -> list[Violation]:
        """Everything that must be true of the log, checked against the log."""
        events = self._log.read_all()
        violations: list[Violation] = []

        # Points are conserved and minted only here. A negative balance means
        # an actor spent points it was never given.
        balances: dict[str, int] = defaultdict(int)
        for e in events:
            if e.type == ev.CREDITS_TRANSFERRED:
                balances[e.payload["from_actor_id"]] -= e.payload["amount"]
                balances[e.payload["to_actor_id"]] += e.payload["amount"]
        for actor, balance in balances.items():
            if balance < 0 and actor not in (HOUSE_ACTOR_ID, ACCOUNTANT_ACTOR_ID):
                violations.append(Violation(
                    "points_not_conserved",
                    f"{actor} holds {balance}; only the accountant may mint",
                ))

        # A settlement must have been permitted first. Joined on the match
        # itself (settlement.match_id == decision.action_ref), not on
        # correlation_id: a single correlation can carry a DENY and a later
        # ALLOW side by side (a merchant capped on a full lot retrying
        # smaller), and asking "was there an ALLOW anywhere in this story"
        # would let money move on the match that was actually refused.
        allowed = {
            e.payload["action_ref"]
            for e in events
            if e.type == ev.POLICY_DECIDED and e.payload.get("verdict") == "ALLOW"
        }
        decided = {
            e.payload["action_ref"] for e in events if e.type == ev.POLICY_DECIDED
        }
        for e in events:
            if e.type != ev.SETTLEMENT_INITIATED:
                continue
            if e.payload.get("match_id") not in allowed:
                violations.append(Violation(
                    "ungated_settlement",
                    f"settlement {e.payload['settlement_id']} has no preceding ALLOW",
                ))

        # A match must have reached the gate. Join on POLICY_DECIDED, not on
        # presence: MATCH_PROPOSED precedes the gate by design, so a DENIED
        # match is in the log legitimately and must not be flagged.
        for e in events:
            if e.type != ev.MATCH_PROPOSED:
                continue
            if e.payload.get("match_id") not in decided:
                violations.append(Violation(
                    "orphaned_match",
                    f"match {e.payload.get('match_id')} never reached the gate",
                ))

        # The incremental projection must still agree with the authority.
        if self._exchange is not None:
            from exchange.projections import fold

            if self._exchange.state() != fold(events):
                violations.append(Violation(
                    "projection_drift",
                    "the cached projection disagrees with a full fold of the log",
                ))

        if violations:
            for v in violations:
                self._log.append(
                    ACCOUNTANT_ACTOR_ID, ev.INVARIANT_VIOLATED,
                    {"kind": v.kind, "detail": v.detail},
                    correlation_id="invariants",
                )
        return violations
