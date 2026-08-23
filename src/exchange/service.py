"""The exchange service — the single chokepoint through which money moves.

Nothing settles without passing `execute_match`, and `execute_match` always
records its policy decision before acting. That ordering is the audit trail's
guarantee: the gate is visible even when it says yes.
"""
from __future__ import annotations

from dataclasses import asdict, replace

from exchange import events as ev
from exchange import policy
from exchange.eventlog import EventLog
from exchange.models import (
    Actor,
    Asset,
    Currency,
    Match,
    Order,
    PolicyDecision,
    Settlement,
    SettlementStatus,
    Verdict,
)
from exchange.policy import PolicyContext
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
    ) -> None:
        self.log = log
        self.index = index
        self._inr_rail = inr_rail
        self._credit_rail = credit_rail
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

        decision = policy.evaluate(
            action_ref=match.match_id,
            actor_id=buyer_id,
            amount=amount,
            currency=currency,
            ctx=replace(ctx, rolling_spend=spent),
            limits=limits,
        )

        decision_event = self.log.append(
            buyer_id,
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

        return decision, settlement

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
