"""Fold the event log into queryable state.

Nothing here holds state of its own. Every value is derived from the log,
so the log and the state can never disagree.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Iterable

from exchange import events as ev
from exchange.events import Event
from exchange.models import (
    Actor,
    ActorKind,
    ActorStatus,
    Asset,
    AssetKind,
    Currency,
    Match,
    Order,
    Settlement,
    SettlementStatus,
    Side,
)


@dataclass(frozen=True)
class ExchangeState:
    actors: dict[str, Actor] = field(default_factory=dict)
    assets: dict[str, Asset] = field(default_factory=dict)
    open_orders: dict[str, Order] = field(default_factory=dict)
    credit_balances: dict[str, int] = field(default_factory=dict)
    settlements: dict[str, Settlement] = field(default_factory=dict)
    matches: dict[str, Match] = field(default_factory=dict)


def fold(events: Iterable[Event]) -> ExchangeState:
    actors: dict[str, Actor] = {}
    assets: dict[str, Asset] = {}
    open_orders: dict[str, Order] = {}
    balances: dict[str, int] = defaultdict(int)
    settlements: dict[str, Settlement] = {}
    matches: dict[str, Match] = {}

    for event in events:
        p = event.payload

        if event.type == ev.ACTOR_REGISTERED:
            actors[p["actor_id"]] = Actor(
                actor_id=p["actor_id"],
                kind=ActorKind(p["kind"]),
                merchant_id=p.get("merchant_id"),
                plan_tier=p.get("plan_tier", "standard"),
                status=ActorStatus(p.get("status", "ACTIVE")),
            )

        elif event.type == ev.ASSET_LISTED:
            assets[p["asset_id"]] = Asset(
                asset_id=p["asset_id"],
                kind=AssetKind(p["kind"]),
                title=p["title"],
                spec=p.get("spec", {}),
                currency=Currency(p["currency"]),
                origin_actor_id=p["origin_actor_id"],
            )

        elif event.type == ev.ORDER_POSTED:
            open_orders[p["order_id"]] = Order(
                order_id=p["order_id"],
                actor_id=p["actor_id"],
                side=Side(p["side"]),
                asset_ref=p.get("asset_ref"),
                asset_query=p.get("asset_query"),
                qty=p["qty"],
                limit_price=p["limit_price"],
                currency=Currency(p["currency"]),
                expires_at=p["expires_at"],
                policy_snapshot=p.get("policy_snapshot", {}),
            )

        elif event.type == ev.ORDER_EXPIRED:
            open_orders.pop(p["order_id"], None)

        elif event.type == ev.MATCH_PROPOSED:
            matches[p["match_id"]] = Match(
                match_id=p["match_id"],
                bid_order_id=p["bid_order_id"],
                ask_order_id=p["ask_order_id"],
                clearing_price=p["clearing_price"],
                score=p["score"],
                rationale=p["rationale"],
            )

        elif event.type == ev.CREDITS_TRANSFERRED:
            balances[p["from_actor_id"]] -= p["amount"]
            balances[p["to_actor_id"]] += p["amount"]

        elif event.type == ev.SETTLEMENT_INITIATED:
            settlements[p["settlement_id"]] = Settlement(
                settlement_id=p["settlement_id"],
                match_id=p["match_id"],
                currency=Currency(p["currency"]),
                amount=p["amount"],
                status=SettlementStatus.PENDING,
                razorpay_order_id=p.get("razorpay_order_id"),
            )

        elif event.type == ev.SETTLEMENT_COMPLETED:
            existing = settlements[p["settlement_id"]]
            settlements[p["settlement_id"]] = replace(
                existing,
                status=SettlementStatus.COMPLETED,
                razorpay_payment_id=p.get("razorpay_payment_id"),
            )

        elif event.type == ev.SETTLEMENT_FAILED:
            existing = settlements[p["settlement_id"]]
            settlements[p["settlement_id"]] = replace(
                existing, status=SettlementStatus.FAILED
            )

    return ExchangeState(
        actors=actors,
        assets=assets,
        open_orders=open_orders,
        credit_balances=dict(balances),
        settlements=settlements,
        matches=matches,
    )
