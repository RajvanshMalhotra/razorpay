"""The exchange service — the single chokepoint through which money moves.

No money moves without passing `execute_match`, and `execute_match` always
records its policy decision before acting. That ordering is the audit trail's
guarantee: the gate is visible even when it says yes.

Said precisely, because the claim is worth more than its rhetoric: every
SETTLEMENT_INITIATED — the moment exposure is committed on either rail — is
written by a rail that only `execute_match` calls. The one other writer of a
settlement event is `Accountant.repair`, which appends SETTLEMENT_COMPLETED
for a settlement that already passed this gate, from a capture Razorpay
confirms. It records an outcome; it cannot open an exposure.

BOTH RAILS, not just rupees. Points convert to Razorpay fee rebates, so a
points transfer is a money action and goes through here too — the insight
auction's purchase and its royalty payouts included. The auction used to pay
out with a raw `log.append`, which put the entire points economy outside the
gate and outside the accountant's invariants; the one flow built to showcase
the gate was the only flow that never fired it.

A settled INR trade also MINTS here, through the accountant. Earning is the
other half of the economy and the settlement is the moment it is established.
"""
from __future__ import annotations

from dataclasses import asdict, replace

from exchange import events as ev
from exchange import policy
from exchange.eventlog import EventLog
from exchange.house.accountant import Accountant
from exchange.house.points import points_for_settlement
from exchange.models import (
    Actor,
    ActorStatus,
    Asset,
    Currency,
    Match,
    Order,
    PolicyDecision,
    Settlement,
    SettlementStatus,
    Verdict,
)
from exchange.policy import GATE_ACTOR_ID, PolicyContext
from exchange.projections import ExchangeState, fold, fold_from
from exchange.retrieval import HybridIndex


class Exchange:
    def __init__(
        self,
        log: EventLog,
        index: HybridIndex,
        inr_rail,
        credit_rail,
        inr_limits: policy.PolicyLimits | None = None,
        credit_limits: policy.PolicyLimits | None = None,
        minter: Accountant | None = None,
    ) -> None:
        self.log = log
        self.index = index
        self._inr_rail = inr_rail
        self._credit_rail = credit_rail
        # Wired by default, not optionally. Earning is half the economy, and an
        # optional minter is a minter someone forgets to pass — which is how
        # `points_for_settlement` came to be a well-tested function with no
        # caller. Minting reads and writes the log only, so the accountant
        # needs no Razorpay client to do it.
        self._minter = minter or Accountant(log, client=None)
        # Limits are configuration, set once per exchange. Deliberately not a
        # parameter of execute_match: a caller must not be able to hand the gate
        # the caps it would like applied to itself.
        self._inr_limits = inr_limits or policy.DEFAULT_INR_LIMITS
        self._credit_limits = credit_limits or policy.DEFAULT_CREDIT_LIMITS
        self._indexed: list[tuple[str, str]] = []

    def register_actor(self, actor: Actor) -> None:
        self.log.append(
            actor.actor_id,
            ev.ACTOR_REGISTERED,
            _serialize(actor),
            correlation_id=f"reg_{actor.actor_id}",
        )

    def list_asset(self, asset: Asset) -> None:
        self.log.append(
            asset.origin_actor_id,
            ev.ASSET_LISTED,
            _serialize(asset),
            correlation_id=f"lst_{asset.asset_id}",
        )
        self._indexed.append((asset.asset_id, f"{asset.title} {_spec_text(asset)}"))
        self.index.index(self._indexed)

    def post_order(self, order: Order, correlation_id: str) -> None:
        self.log.append(
            order.actor_id,
            ev.ORDER_POSTED,
            _serialize(order),
            correlation_id=correlation_id,
        )

    def execute_match(
        self,
        match: Match,
        buyer_id: str,
        seller_id: str,
        ctx: PolicyContext,
        correlation_id: str,
        currency: Currency = Currency.INR,
    ) -> tuple[PolicyDecision, Settlement | None]:
        limits = self._inr_limits if currency == Currency.INR else self._credit_limits

        # A match_id reaches the gate at most once. `assert_invariants` joins
        # settlements to decisions on match_id — rather than on correlation_id —
        # exactly so that a DENY and a later ALLOW on one story cannot be
        # confused for each other; that join is only sound while the id is
        # unique. A retry at different terms is a NEW match and must be minted
        # as one (`matching.resize`), so a repeat here is a caller bug rather
        # than a market event. Refused before anything is written: logging a
        # second decision under the same action_ref would create the very
        # ambiguity the join exists to prevent.
        if self._already_decided(match.match_id):
            raise ValueError(
                f"match {match.match_id} has already been through the gate; "
                "a retry at different terms needs a fresh match_id"
            )

        # `clearing_price` is PER UNIT — the matcher sets it from the ask's
        # limit_price and compares limits across orders of different sizes. The
        # exposure a cap must bound, and the figure the rail must charge, is the
        # whole lot. Gating 500 units at 1940 as if it were 1940 let every trade
        # slip under the unknown-counterparty cap, so the trial-size bound — the
        # entire anti-incumbency mechanism — never bound anything.
        amount = match.clearing_price * match.qty

        # The match itself is an event. Without it the decision's action_ref
        # dangles and the rationale — the one artifact that explains *why* this
        # ask at this price — never reaches the audit trail.
        match_event = self.log.append(
            buyer_id,
            ev.MATCH_PROPOSED,
            _serialize(match),
            correlation_id=correlation_id,
        )

        # Derived from the log, never taken from the caller. A cap the actor
        # supplies its own usage figure for is not a cap.
        spent = self._spend_to_date(buyer_id, currency)

        # Same reasoning, same authority: a status the actor asserts about
        # itself is not a status. The accountant freezes a merchant by
        # appending ACTOR_FROZEN, and the freeze has to bind the frozen
        # broker's very next money action — which it can only do if the gate
        # reads the projection instead of the argument. Whatever the caller
        # put in `ctx.actor_status` is discarded here.
        status = self._status_of(buyer_id)

        decision = policy.evaluate(
            action_ref=match.match_id,
            actor_id=buyer_id,
            amount=amount,
            currency=currency,
            ctx=replace(ctx, rolling_spend=spent, actor_status=status),
            limits=limits,
        )

        decision_event = self.log.append(
            GATE_ACTOR_ID,
            ev.POLICY_DECIDED,
            _serialize(decision),
            correlation_id=correlation_id,
            causation_id=match_event.event_id,
        )

        if decision.verdict != Verdict.ALLOW:
            return decision, None

        rail = self._inr_rail if currency == Currency.INR else self._credit_rail
        settlement = rail.settle(
            match_id=match.match_id,
            from_actor_id=buyer_id,
            to_actor_id=seller_id,
            amount=amount,
            correlation_id=correlation_id,
            causation_id=decision_event.event_id,
        )

        if settlement is not None and settlement.status == SettlementStatus.COMPLETED:
            self._record_fill(match, buyer_id, seller_id, correlation_id, decision_event.event_id)
            self._mint_earned(match, buyer_id, settlement, currency,
                              correlation_id, decision_event.event_id)

        return decision, settlement

    def _mint_earned(self, match: Match, buyer_id: str, settlement: Settlement,
                     currency: Currency, correlation_id: str, causation_id: str) -> None:
        """Trading well earns points, at the moment the trade actually settles.

        On the trade's own correlation id, so "what did this deal earn" is
        answered by replaying the deal rather than by knowing where else to
        look — the same reasoning that put DRIFT_DETECTED on the trade's
        thread.

        INR ONLY. Points are earned by trading goods well; minting for a
        points-denominated purchase would pay merchants to spend points and
        turn the economy into a loop that funds itself.

        The ask price comes from the ORDER_POSTED event, not from
        `match.clearing_price` — the broker overwrites the clearing price with
        the negotiated figure, so the match no longer remembers what was
        asked, and the margin this rule pays for is exactly the difference
        between the two. Read from the log rather than from the book because
        a filled ask leaves the book.

        No ask on record means no margin can be established, and an
        unestablished margin is not a zero one: nothing is minted rather than
        BASE_POINTS being paid on an unknown. That is the same refusal to
        invent a fact the record does not hold that governs the rest of this
        system.
        """
        if currency != Currency.INR:
            return

        ask_price = self._posted_limit(match.ask_order_id)
        if ask_price is None:
            return

        points = points_for_settlement(
            amount=settlement.amount,
            ask_price=ask_price,
            qty=match.qty,
            delivered=settlement.status == SettlementStatus.COMPLETED,
        )
        if points <= 0:
            return

        self._minter.mint(
            actor_id=buyer_id,
            points=points,
            source_settlement_id=settlement.settlement_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            reason=(
                f"margin captured on {settlement.settlement_id}: paid "
                f"{settlement.amount} against an ask of {ask_price} x {match.qty}"
            ),
        )

    def _posted_limit(self, order_id: str) -> int | None:
        """The per-unit limit this order was posted at, read from the log."""
        for event in self.log.read_all():
            if (
                event.type == ev.ORDER_POSTED
                and event.payload.get("order_id") == order_id
            ):
                return event.payload["limit_price"]
        return None

    def _already_decided(self, match_id: str) -> bool:
        """Has this match_id already carried a policy decision?"""
        return any(
            e.type == ev.POLICY_DECIDED and e.payload.get("action_ref") == match_id
            for e in self.log.read_all()
        )

    def _status_of(self, actor_id: str) -> ActorStatus:
        """This actor's status as the log records it.

        An actor with no ACTOR_REGISTERED event has nothing frozen on record,
        so it is treated as ACTIVE: absence of a registration is not a freeze,
        and whether an unregistered actor may trade at all is a different
        question this gate does not answer. A freeze, once appended, is what
        `fold` projects until an ACTOR_RESUMED follows it.
        """
        actor = self.state().actors.get(actor_id)
        return actor.status if actor is not None else ActorStatus.ACTIVE

    def _spend_to_date(self, actor_id: str, currency: Currency) -> int:
        """Total this actor has already committed on this rail, read from the log.

        NOTE: this is a *cumulative* spend, not a time-windowed one. The spec
        calls for a rolling window; summing every settlement ever initiated is
        strictly tighter than any window over the same log, so the cap can only
        bind sooner, never later — safe, but not the same thing. The time bound
        is a later refinement; until it lands the cap is cumulative per actor
        per currency.

        Counted at SETTLEMENT_INITIATED rather than at completion: money is
        committed the moment a Razorpay order exists, and a settlement sitting
        PENDING is exactly the exposure a cap is meant to bound.
        """
        target = str(currency)
        return sum(
            e.payload["amount"]
            for e in self.log.read_all()
            if e.type == ev.SETTLEMENT_INITIATED
            and e.actor_id == actor_id
            and e.payload.get("currency") == target
        )

    def _record_fill(self, match: Match, buyer_id: str, seller_id: str,
                     correlation_id: str, causation_id: str) -> None:
        """Deplete both orders. Each side is recorded against its own actor.

        Sides are handled independently on purpose: if one order is already gone
        from the book, the other must still be depleted, or it can be re-settled
        against the same inventory indefinitely.
        """
        book = self.state().open_orders
        for order_id, actor_id in (
            (match.bid_order_id, buyer_id),
            (match.ask_order_id, seller_id),
        ):
            if order_id not in book:
                continue
            self.log.append(
                actor_id,
                ev.ORDER_FILLED,
                {"order_id": order_id, "qty": match.qty},
                correlation_id=correlation_id,
                causation_id=causation_id,
            )

    def state(self) -> ExchangeState:
        """Current state, extended from the cached projection.

        This re-reads via `read_since` on every call, so a second `Exchange`
        over the same database, or anything appending to the log directly,
        cannot cause divergence — their events are picked up on the next read.

        The real precondition is a SINGLE WRITER CONNECTION. Two concurrent
        SQLite writers could commit seq 10 after seq 11 became visible; a read
        landing in between would advance the offset past 10 and skip it
        permanently. The accountant's periodic full rebuild from the log is
        what would catch that, and `fold()` remains the authority.
        """
        cached = getattr(self, "_state_cache", None)
        if cached is None:
            self._state_cache = fold(self.log.read_all())
        else:
            new_events = self.log.read_since(cached.event_offset)
            if new_events:
                self._state_cache = fold_from(cached, new_events)
        return self._state_cache


def _serialize(record) -> dict:
    """Dataclass to JSON-safe dict. StrEnum members serialize as their value."""
    return {k: (str(v) if hasattr(v, "value") else v) for k, v in asdict(record).items()}


def _spec_text(asset: Asset) -> str:
    return " ".join(str(v) for v in asset.spec.values())
