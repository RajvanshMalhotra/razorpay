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
from dataclasses import dataclass, field

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.points import OPENING_GRANT_CAP

ACCOUNTANT_ACTOR_ID = "accountant"


@dataclass(frozen=True)
class Drift:
    """Captured upstream, still PENDING here — the dropped webhook.

    REPAIRABLE, and repairable for a specific reason: the remote is the
    authority on whether money moved, the remote says it did, and recording
    that is telling the truth about a fact that already exists. `repair()`
    takes this and only this.
    """
    settlement_id: str
    local_status: str
    remote_status: str
    correlation_id: str | None = None
    razorpay_order_id: str | None = None


@dataclass(frozen=True)
class UnbackedCompletion:
    """COMPLETED here, and the remote shows no captured payment.

    A SEPARATE TYPE FROM `Drift`, deliberately and permanently. These are not
    two flavours of one problem; they are opposites, and the same code must
    never handle both:

    - A `Drift` is repaired by writing what the remote confirms.
    - An `UnbackedCompletion` CANNOT be repaired by anything here. The wrong
      record is a `SETTLEMENT_COMPLETED` that is already in an append-only
      log; it cannot be withdrawn, and writing a second completion the remote
      denies is precisely what `repair()` refuses to do. Passing one to
      `repair()` raises.

    It is the dangerous direction. `HouseAgent.observe` mines only completed
    settlements, and the memory loop reads a clean settlement as a delivery
    signal — so an unbacked completion quietly becomes evidence of
    reliability, gets sold on as market intelligence, earns its "contributor"
    a royalty, and raises the trial cap for a merchant that may never have
    paid. Left undetected it does not sit still; it compounds.

    `actor_id` is whoever initiated the settlement — the party the books
    currently credit with having paid, and therefore the party to stop.
    """
    settlement_id: str
    actor_id: str
    local_status: str
    remote_status: str
    correlation_id: str | None = None
    razorpay_order_id: str | None = None


@dataclass(frozen=True)
class Reconciliation:
    """What one reconciliation run found, with the two directions kept apart.

    Deliberately NOT a flat list. A caller iterating one sequence of "problems"
    is one `isinstance` away from repairing the thing that must not be
    repaired; splitting them at the type level means a caller has to name which
    direction it is handling before it can touch anything.
    """
    drifts: list[Drift] = field(default_factory=list)
    unbacked: list[UnbackedCompletion] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.drifts and not self.unbacked


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

    def reconcile(self) -> Reconciliation:
        """Compare local settlement records against Razorpay's own state.

        BOTH DIRECTIONS, because only one of them is safe to miss.

        - PENDING here, captured upstream: the dropped webhook. Repairable,
          and what the failure demo turns on.
        - COMPLETED here, no captured payment upstream: an unbacked
          completion. This used to be computed and thrown away — the loop
          asked only about the direction the demo needed. It is the one that
          costs money: the books say a merchant paid, the remote says it did
          not, and every downstream reader (the insight miner, the memory
          loop, the trial cap) treats the local record as fact.

        The response to the second is `_contain_unbacked`, not repair. See
        `UnbackedCompletion` for why the two can never share a code path.
        """
        events = self._log.read_all()
        completed = {
            e.payload["settlement_id"]
            for e in events if e.type == ev.SETTLEMENT_COMPLETED
        }
        # An unbacked completion cannot be undone — the log is append-only —
        # so the condition holds on every later run. It is REPORTED every run
        # (the books are still wrong) but only ACTED ON once: re-freezing an
        # already-frozen actor on every reconciliation would bury the trade's
        # thread in duplicates of one event.
        already_contained = {
            e.payload["settlement_id"]
            for e in events if e.type == ev.UNBACKED_COMPLETION_DETECTED
        }

        drifts: list[Drift] = []
        unbacked: list[UnbackedCompletion] = []
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
                drift = Drift(
                    settlement_id=sid,
                    local_status=local,
                    remote_status=remote,
                    correlation_id=event.correlation_id,
                    razorpay_order_id=order_id,
                )
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
            elif local == "COMPLETED" and remote != "captured":
                found = UnbackedCompletion(
                    settlement_id=sid,
                    actor_id=event.actor_id,
                    local_status=local,
                    remote_status=remote,
                    correlation_id=event.correlation_id,
                    razorpay_order_id=order_id,
                )
                unbacked.append(found)
                if sid not in already_contained:
                    self._contain_unbacked(found, event)

        self._log.append(
            ACCOUNTANT_ACTOR_ID, ev.RECONCILED,
            {"settlements_checked": checked, "drifts": len(drifts),
             "unbacked_completions": len(unbacked)},
            correlation_id="recon",
        )
        return Reconciliation(drifts=drifts, unbacked=unbacked)

    def _contain_unbacked(self, found: UnbackedCompletion, initiated) -> None:
        """Record an unbacked completion and stop the actor it credits.

        CONTAIN, NOT REPAIR — there is nothing to repair. The false record is
        already in an append-only log and the honest response is to say so,
        loudly, in three places at once:

        1. `UNBACKED_COMPLETION_DETECTED` on the TRADE's own correlation, so a
           judge replaying that trade sees the completion contradicted right
           where the completion is, rather than having to know that a
           reconciliation index exists.
        2. A FREEZE, and not an optional one. A `Drift` is repairable, so
           whether to freeze on one is a judgment its caller makes. This is
           not: no code path in this system can make these books honest again,
           so leaving the response to a caller means the only available
           response is one nobody is obliged to take. Meanwhile the record is
           already live — feeding the insight miner, earning royalties, and
           ratcheting a trial cap on a payment the remote denies. The freeze
           is what bounds that, and it binds because `execute_match` derives
           `actor_status` from the log for itself.
        3. `assert_invariants` reports it as a violation on every subsequent
           run. A freeze can be lifted by a resume; the fact that the books
           contain a completion nobody was paid for cannot be, and an auditor
           that mentions it once is an auditor that lets it be forgotten.
        """
        detected = self._log.append(
            ACCOUNTANT_ACTOR_ID, ev.UNBACKED_COMPLETION_DETECTED,
            {"settlement_id": found.settlement_id,
             "actor_id": found.actor_id,
             "local_status": found.local_status,
             "remote_status": found.remote_status,
             "razorpay_order_id": found.razorpay_order_id},
            correlation_id=initiated.correlation_id,
            causation_id=initiated.event_id,
        )
        self.freeze(
            found.actor_id,
            reason=(
                f"settlement {found.settlement_id} is COMPLETED locally but "
                f"Razorpay shows no captured payment on "
                f"{found.razorpay_order_id}"
            ),
            correlation_id=initiated.correlation_id,
            causation_id=detected.event_id,
        )

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

        # A completion the remote denied. `reconcile()` writes
        # UNBACKED_COMPLETION_DETECTED when it finds one; this reports it
        # forever after, because the log cannot be un-appended and the books
        # therefore contain a payment that was never made. Deliberately not
        # cleared by the resume that lifts the freeze: the freeze is a
        # containment measure and is meant to end, the false record is not.
        for e in events:
            if e.type != ev.UNBACKED_COMPLETION_DETECTED:
                continue
            violations.append(Violation(
                "unbacked_completion",
                f"settlement {e.payload['settlement_id']} is COMPLETED locally "
                f"but Razorpay shows no captured payment on "
                f"{e.payload.get('razorpay_order_id')}",
            ))

        # A completion with no initiation. `fold` no longer raises on one —
        # see projections.py — but "does not crash the audit trail" is not the
        # same as "is fine", and the settlement it projects has no known
        # amount, match or currency to check anything against.
        initiated_ids = {
            e.payload["settlement_id"]
            for e in events if e.type == ev.SETTLEMENT_INITIATED
        }
        for e in events:
            if e.type != ev.SETTLEMENT_COMPLETED:
                continue
            sid = e.payload["settlement_id"]
            if sid not in initiated_ids:
                violations.append(Violation(
                    "orphaned_completion",
                    f"settlement {sid} completed with no SETTLEMENT_INITIATED; "
                    "there is no record of what was owed or to whom",
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

    def freeze(
        self,
        actor_id: str,
        reason: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> None:
        """Stop an actor trading until its books agree again.

        Per-actor, never global: one merchant's drift must not stop the market.

        Enforcement, not advice: this event is the whole mechanism.
        `Exchange.execute_match` folds the buyer's status out of the log for
        itself and hands that to the gate, discarding whatever status the
        caller supplied — so ACTOR_FROZEN denies the frozen merchant's very
        next money action no matter what its broker claims about itself. A
        matching ACTOR_RESUMED is what lifts it.

        WHICH THREAD THE FREEZE BELONGS ON is a genuine question and the
        answer is "it depends", which is why `correlation_id` is optional
        rather than either hard-coded or required:

        - A freeze caused by ONE specific trade is a chapter of that trade's
          story. Pass that trade's correlation and a replay of the failure
          shows initiated -> drift -> frozen -> completed -> resumed, which is
          the whole point of the failure demo. Filed elsewhere, the two middle
          chapters simply are not in the story the video tells.
        - A freeze that is NOT about one trade — an actor-level suspension, a
          manual intervention, a pattern across several deals — genuinely
          spans more than one correlation, and nailing it to whichever trade
          happened to be last would be a lie about what caused it.

        So the default stays `freeze_{actor_id}`: an actor-scoped index of
        every freeze and resume, which remains a useful second index even when
        a freeze is also threaded onto a trade.
        """
        self._log.append(
            ACCOUNTANT_ACTOR_ID, ev.ACTOR_FROZEN,
            {"actor_id": actor_id, "reason": reason},
            correlation_id=correlation_id or f"freeze_{actor_id}",
            causation_id=causation_id,
        )

    def repair(self, drift: Drift) -> None:
        """Make the local record agree with Razorpay's.

        The remote is the authority for whether money moved — we did not take
        the payment, they did. Repair means recording what actually happened,
        never asserting what we wish had.

        Takes a `Drift` and nothing else. An `UnbackedCompletion` is the
        opposite problem and is refused here explicitly rather than left to
        fail somewhere further in: "repairing" one would mean appending a
        completion the remote denies, which is the exact hazard the null
        payment-id refusal below exists to prevent, arriving through the front
        door instead.
        """
        if isinstance(drift, UnbackedCompletion):
            raise ValueError(
                f"refusing to repair {drift.settlement_id}: an unbacked "
                "completion is not a drift. The local record already claims "
                "money moved and the remote denies it; there is no truth here "
                "to write down. It is contained by a freeze and reported as a "
                "violation, not repaired."
            )
        if not isinstance(drift, Drift):
            raise TypeError(
                f"repair() takes a Drift, got {type(drift).__name__}"
            )

        events = self._log.read_all()

        # Idempotent by refusal, not by silence. A second repair used to
        # append a second SETTLEMENT_COMPLETED for one settlement: the fold
        # absorbs it, so nothing crashes, and the audit trail quietly grows a
        # duplicate chapter that a reader has to work out is not two payments.
        if any(
            e.type == ev.SETTLEMENT_COMPLETED
            and e.payload["settlement_id"] == drift.settlement_id
            for e in events
        ):
            raise ValueError(
                f"settlement {drift.settlement_id} is already COMPLETED in the "
                "log; a second repair would append a second completion for one "
                "payment"
            )

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

    def resume(
        self,
        actor_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> None:
        """Lift a freeze. Threaded exactly like `freeze` and for the reasons
        given there — a resume that ends one trade's failure belongs on that
        trade, and one that ends an actor-level suspension does not.

        A resume filed away from the freeze it lifts is the worse half of the
        bug: the trail then shows a merchant that stopped trading and no
        record of anyone deciding it could start again.
        """
        self._log.append(
            ACCOUNTANT_ACTOR_ID, ev.ACTOR_RESUMED,
            {"actor_id": actor_id},
            correlation_id=correlation_id or f"freeze_{actor_id}",
            causation_id=causation_id,
        )
