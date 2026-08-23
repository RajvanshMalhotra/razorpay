"""Domain records. All amounts are integers in minor units — never floats."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


# These must stay StrEnum: service._serialize and event-payload JSON encoding
# both depend on every member being a str.
class ActorKind(StrEnum):
    MERCHANT = "MERCHANT"
    HOUSE = "HOUSE"
    ACCOUNTANT = "ACCOUNTANT"
    HUMAN = "HUMAN"


class ActorStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"


class AssetKind(StrEnum):
    GOODS = "GOODS"
    SERVICE = "SERVICE"
    INSIGHT = "INSIGHT"


class Currency(StrEnum):
    INR = "INR"
    CREDITS = "CREDITS"


class Side(StrEnum):
    BID = "BID"
    ASK = "ASK"


class Verdict(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"


class SettlementStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def currency_for_kind(kind: AssetKind) -> Currency:
    """INSIGHT trades in points; everything physical trades in rupees."""
    return Currency.CREDITS if kind == AssetKind.INSIGHT else Currency.INR


@dataclass(frozen=True)
class Actor:
    actor_id: str
    kind: ActorKind
    merchant_id: str | None = None
    plan_tier: str = "standard"
    status: ActorStatus = ActorStatus.ACTIVE


@dataclass(frozen=True)
class Asset:
    asset_id: str
    kind: AssetKind
    title: str
    spec: dict[str, Any]
    currency: Currency
    origin_actor_id: str

    def __post_init__(self) -> None:
        expected = currency_for_kind(self.kind)
        if self.currency != expected:
            raise ValueError(
                f"{self.kind} assets must be priced in {expected}, got {self.currency}"
            )


@dataclass(frozen=True)
class Order:
    order_id: str
    actor_id: str
    side: Side
    asset_ref: str | None
    asset_query: dict[str, Any] | None
    qty: int
    limit_price: int
    currency: Currency
    expires_at: str
    policy_snapshot: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.asset_ref is None) == (self.asset_query is None):
            raise ValueError(
                "An order must carry exactly one of asset_ref or asset_query"
            )

    @property
    def is_descriptive(self) -> bool:
        return self.asset_query is not None


@dataclass(frozen=True)
class Match:
    match_id: str
    bid_order_id: str
    ask_order_id: str
    clearing_price: int
    qty: int
    score: float
    rationale: str


@dataclass(frozen=True)
class Settlement:
    settlement_id: str
    match_id: str
    currency: Currency
    amount: int
    status: SettlementStatus
    razorpay_order_id: str | None = None
    razorpay_payment_id: str | None = None
    reconciled_at: str | None = None
    reconciliation_status: str | None = None


@dataclass(frozen=True)
class PolicyDecision:
    decision_id: str
    action_ref: str
    actor_id: str
    verdict: Verdict
    reason: str
    limits_evaluated: dict[str, Any]
    ts: str


@dataclass(frozen=True)
class CreditLedgerEntry:
    entry_id: str
    actor_id: str
    delta: int
    reason: str
    source_settlement_id: str | None
    ts: str


@dataclass(frozen=True)
class RelationshipEdge:
    from_actor_id: str
    to_actor_id: str
    deals_count: int = 0
    total_value: int = 0
    reliability_score: float = 0.5
    confidence: float = 0.0
    last_interaction_at: str | None = None
    lessons_ref: str | None = None
