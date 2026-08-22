"""The CREDITS rail — an atomic ledger transfer.

Points never leave the system, so settlement is a single balance check
followed by a transfer event. Conservation is the invariant that matters.
"""
from __future__ import annotations

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.ids import new_id
from exchange.models import Currency, Settlement, SettlementStatus
from exchange.projections import fold
from exchange.rails.base import InsufficientCredits


class CreditRail:
    def __init__(self, log: EventLog) -> None:
        self._log = log

    def settle(
        self,
        match_id: str,
        from_actor_id: str,
        to_actor_id: str,
        amount: int,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> Settlement:
        balance = fold(self._log.read_all()).credit_balances.get(from_actor_id, 0)
        if balance < amount:
            raise InsufficientCredits(
                f"{from_actor_id} holds {balance} points, needs {amount}"
            )

        settlement_id = new_id("stl")

        initiated = self._log.append(
            from_actor_id,
            ev.SETTLEMENT_INITIATED,
            {
                "settlement_id": settlement_id,
                "match_id": match_id,
                "currency": str(Currency.CREDITS),
                "amount": amount,
            },
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

        transferred = self._log.append(
            from_actor_id,
            ev.CREDITS_TRANSFERRED,
            {
                "from_actor_id": from_actor_id,
                "to_actor_id": to_actor_id,
                "amount": amount,
                "settlement_id": settlement_id,
            },
            correlation_id=correlation_id,
            causation_id=initiated.event_id,
        )

        self._log.append(
            from_actor_id,
            ev.SETTLEMENT_COMPLETED,
            {"settlement_id": settlement_id},
            correlation_id=correlation_id,
            causation_id=transferred.event_id,
        )

        return Settlement(
            settlement_id=settlement_id,
            match_id=match_id,
            currency=Currency.CREDITS,
            amount=amount,
            status=SettlementStatus.COMPLETED,
        )
