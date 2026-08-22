"""The settlement rail interface, shared by both currencies."""
from __future__ import annotations

from typing import Protocol

from exchange.models import Settlement


class InsufficientCredits(Exception):
    """Raised when an actor's point balance cannot cover a transfer."""


class SettlementRail(Protocol):
    def settle(
        self,
        match_id: str,
        from_actor_id: str,
        to_actor_id: str,
        amount: int,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> Settlement:
        ...
