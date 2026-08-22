"""The exchange service — the single chokepoint through which money moves.

Nothing settles without passing `execute_match`, and `execute_match` always
records its policy decision before acting. That ordering is the audit trail's
guarantee: the gate is visible even when it says yes.
"""
from __future__ import annotations

from dataclasses import asdict

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
from exchange.projections import ExchangeState, fold
from exchange.retrieval import HybridIndex


class Exchange:
    def __init__(
        self,
        log: EventLog,
        index: HybridIndex,
        inr_rail,
        credit_rail,
    ) -> None:
        self.log = log
        self.index = index
        self._inr_rail = inr_rail
        self._credit_rail = credit_rail
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
        limits = (
            policy.DEFAULT_INR_LIMITS
            if currency == Currency.INR
            else policy.DEFAULT_CREDIT_LIMITS
        )

        # The match itself is an event. Without it the decision's action_ref
        # dangles and the rationale — the one artifact that explains *why* this
        # ask at this price — never reaches the audit trail.
        match_event = self.log.append(
            buyer_id,
            ev.MATCH_PROPOSED,
            _serialize(match),
            correlation_id=correlation_id,
        )

        decision = policy.evaluate(
            action_ref=match.match_id,
            actor_id=buyer_id,
            amount=match.clearing_price,
            currency=currency,
            ctx=ctx,
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
            amount=match.clearing_price,
            correlation_id=correlation_id,
            causation_id=decision_event.event_id,
        )

        if settlement is not None and settlement.status == SettlementStatus.COMPLETED:
            self._record_fill(match, buyer_id, correlation_id, decision_event.event_id)

        return decision, settlement

    def _record_fill(
        self,
        match: Match,
        buyer_id: str,
        correlation_id: str,
        causation_id: str,
    ) -> None:
        """Deplete both sides of a settled trade.

        Without this both orders stay open at full quantity, and a broker that
        folds open_orders and re-runs matching in a loop re-settles the same ask
        against the same inventory forever — creating a real Razorpay order each
        time. Only the success path fills: a DENY, a REQUIRE_HUMAN or a failed
        settlement moved nothing and must leave the book untouched.
        """
        bid_order = self.state().open_orders.get(match.bid_order_id)
        if bid_order is None:
            # Nothing in the book to deplete — the caller matched against an
            # order this log does not hold. Skip rather than raise; the
            # settlement itself already stands.
            return

        for order_id in (match.bid_order_id, match.ask_order_id):
            self.log.append(
                buyer_id,
                ev.ORDER_FILLED,
                {
                    "order_id": order_id,
                    "qty": bid_order.qty,
                    "match_id": match.match_id,
                },
                correlation_id=correlation_id,
                causation_id=causation_id,
            )

    def state(self) -> ExchangeState:
        return fold(self.log.read_all())


def _serialize(record) -> dict:
    """Dataclass to JSON-safe dict. StrEnum members serialize as their value."""
    return {k: (str(v) if hasattr(v, "value") else v) for k, v in asdict(record).items()}


def _spec_text(asset: Asset) -> str:
    return " ".join(str(v) for v in asset.spec.values())
