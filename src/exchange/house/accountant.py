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
from exchange.house.points import OPENING_GRANT_CAP

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
                # On the TRADE's correlation, not the reconciliation's. The
                # drift is a chapter in that trade's story; filed under
                # recon_* it would be discoverable only by someone who
                # already knew to go looking, and a replay of the trade
                # would show a settlement that mysteriously fixed itself.
                self._log.append(
                    ACCOUNTANT_ACTOR_ID, ev.DRIFT_DETECTED,
                    {"settlement_id": sid, "local_status": local,
                     "remote_status": remote, "razorpay_order_id": order_id},
                    correlation_id=event.correlation_id,
                    causation_id=event.event_id,
                )

        self._log.append(
            ACCOUNTANT_ACTOR_ID, ev.RECONCILED,
            {"settlements_checked": checked, "drifts": len(drifts)},
            correlation_id="recon",
        )
        return drifts

    def mint(
        self,
        actor_id: str,
        points: int,
        source_settlement_id: str | None,
        correlation_id: str,
        causation_id: str | None = None,
        reason: str = "earned on a settled trade",
    ) -> None:
        """Create points. The only way points enter the economy.

        Points convert to fee rebates, so this is a money action and the
        answer to "where do points come from?" has to be a bounded one.
        Two kinds of mint exist and no third:

        - Against a settled trade. `source_settlement_id` names the
          settlement, the amount comes from `points_for_settlement`, and the
          settlement may be minted against ONCE — a second call for the same
          settlement is refused, so a replayed or retried settlement path
          cannot double-pay.
        - An opening grant (`source_settlement_id=None`), capped at
          `OPENING_GRANT_CAP` and logged with its reason. This stands in for
          earning that predates the log; it is capped rather than free
          because an uncapped grant is exactly the unbounded source this
          method exists to replace.

        The house is not exempt from either rule. It holds a real balance,
        funded by what it sells, and `assert_invariants` now checks it.
        """
        if points <= 0:
            raise ValueError(f"refusing to mint {points} points: a mint is an increase")

        if source_settlement_id is None:
            if points > OPENING_GRANT_CAP:
                raise ValueError(
                    f"refusing an opening grant of {points} to {actor_id}: "
                    f"above the cap of {OPENING_GRANT_CAP}"
                )
        elif self._already_minted(source_settlement_id):
            raise ValueError(
                f"settlement {source_settlement_id} has already been minted "
                "against; a settlement earns points once"
            )

        self._log.append(
            ACCOUNTANT_ACTOR_ID,
            ev.POINTS_MINTED,
            {
                "actor_id": actor_id,
                "points": points,
                "source_settlement_id": source_settlement_id,
                "reason": reason,
            },
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    def _already_minted(self, settlement_id: str) -> bool:
        return any(
            e.type == ev.POINTS_MINTED
            and e.payload.get("source_settlement_id") == settlement_id
            for e in self._log.read_all()
        )

    def assert_invariants(self) -> list[Violation]:
        """Everything that must be true of the log, checked against the log."""
        events = self._log.read_all()
        violations: list[Violation] = []

        # Points are conserved and minted only here. Transfers net to zero;
        # POINTS_MINTED is the only event that adds supply, so a negative
        # balance means an actor spent points it was never given or minted.
        #
        # NOBODY is exempt. The house used to be, which made this check blind
        # to the only actor that actually created points — it conjured them
        # with a raw transfer from an empty balance and the auditor reported
        # zero violations. The house now holds a real balance funded by what
        # it sells, and an overspend by the house is a violation like anyone
        # else's.
        balances: dict[str, int] = defaultdict(int)
        for e in events:
            if e.type == ev.CREDITS_TRANSFERRED:
                balances[e.payload["from_actor_id"]] -= e.payload["amount"]
                balances[e.payload["to_actor_id"]] += e.payload["amount"]
            elif e.type == ev.POINTS_MINTED:
                balances[e.payload["actor_id"]] += e.payload["points"]
        for actor, balance in balances.items():
            if balance < 0:
                violations.append(Violation(
                    "points_not_conserved",
                    f"{actor} holds {balance}; only the accountant may mint",
                ))

        # "Minted only by the accountant" was a docstring claim in two files
        # and a check in none. It is a check now.
        minted_against: set[str] = set()
        for e in events:
            if e.type != ev.POINTS_MINTED:
                continue
            if e.actor_id != ACCOUNTANT_ACTOR_ID:
                violations.append(Violation(
                    "unauthorized_mint",
                    f"{e.actor_id} minted {e.payload.get('points')} points; "
                    "only the accountant may mint",
                ))
            sid = e.payload.get("source_settlement_id")
            if sid is None:
                continue
            if sid in minted_against:
                violations.append(Violation(
                    "duplicate_mint",
                    f"settlement {sid} was minted against more than once",
                ))
            minted_against.add(sid)

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

    def freeze(self, actor_id: str, reason: str) -> None:
        """Stop an actor trading until its books agree again.

        Per-actor, never global: one merchant's drift must not stop the market.

        Enforcement, not advice: this event is the whole mechanism.
        `Exchange.execute_match` folds the buyer's status out of the log for
        itself and hands that to the gate, discarding whatever status the
        caller supplied — so ACTOR_FROZEN denies the frozen merchant's very
        next money action no matter what its broker claims about itself. A
        matching ACTOR_RESUMED is what lifts it.
        """
        self._log.append(
            ACCOUNTANT_ACTOR_ID, ev.ACTOR_FROZEN,
            {"actor_id": actor_id, "reason": reason},
            correlation_id=f"freeze_{actor_id}",
        )

    def repair(self, drift: Drift) -> None:
        """Make the local record agree with Razorpay's.

        The remote is the authority for whether money moved — we did not take
        the payment, they did. Repair means recording what actually happened,
        never asserting what we wish had.
        """
        events = self._log.read_all()
        initiated = next(
            e for e in events
            if e.type == ev.SETTLEMENT_INITIATED
            and e.payload["settlement_id"] == drift.settlement_id
        )
        order_id = initiated.payload["razorpay_order_id"]

        payment_id = None
        for item in self._client.order.payments(order_id).get("items", []):
            if item.get("status") == "captured":
                payment_id = item["id"]
                break

        # No captured payment means the remote does not agree money moved,
        # whatever reconcile() saw a moment ago. Refuse rather than write a
        # completion with a null payment id: a repair tool that can assert
        # unconfirmed payments is worse than no repair tool.
        if payment_id is None:
            raise ValueError(
                f"refusing to complete {drift.settlement_id}: "
                f"no captured payment on {order_id}"
            )

        self._log.append(
            ACCOUNTANT_ACTOR_ID, ev.SETTLEMENT_COMPLETED,
            {"settlement_id": drift.settlement_id, "razorpay_payment_id": payment_id},
            correlation_id=initiated.correlation_id,
            causation_id=initiated.event_id,
        )

    def resume(self, actor_id: str) -> None:
        self._log.append(
            ACCOUNTANT_ACTOR_ID, ev.ACTOR_RESUMED,
            {"actor_id": actor_id},
            correlation_id=f"freeze_{actor_id}",
        )
