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
    event_offset: int = 0


def fold(events: Iterable[Event]) -> ExchangeState:
    """Rebuild state from a complete log starting at seq 1.

    PRECONDITION: `events` must be the whole log from seq 1, in order. Folding
    a partial slice — anything from `read_since(seq)` — is not supported: a
    SETTLEMENT_COMPLETED whose SETTLEMENT_INITIATED fell outside the slice has
    no record to update, and projects as a settlement with no known amount,
    match or currency. It no longer raises, because a fold that can raise on a
    log that cannot be edited is a permanently unreadable audit trail — but a
    partial fold still produces wrong state, and the accountant reports the
    shape as `orphaned_completion`. `fold_from(state, events)` is the way to
    resume from an already-folded state.

    This remains the authority. `fold_from` is an optimisation over it and
    must always agree with it — the accountant's job is to prove that.
    """
    return fold_from(ExchangeState(), events)


def fold_from(state: ExchangeState, events: Iterable[Event]) -> ExchangeState:
    """Apply only `events` to an existing state.

    Cost depends on how many events are new, not on how many exist.
    """
    actors: dict[str, Actor] = dict(state.actors)
    assets: dict[str, Asset] = dict(state.assets)
    open_orders: dict[str, Order] = dict(state.open_orders)
    balances: dict[str, int] = defaultdict(int, state.credit_balances)
    settlements: dict[str, Settlement] = dict(state.settlements)
    matches: dict[str, Match] = dict(state.matches)
    offset = state.event_offset

    for event in events:
        offset = max(offset, event.seq)
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

        elif event.type == ev.ORDER_FILLED:
            # A fill for an order the book no longer holds is not an error: it
            # may have expired, or been fully filled by an earlier event.
            existing = open_orders.get(p["order_id"])
            if existing is not None:
                remaining = existing.qty - p["qty"]
                if remaining <= 0:
                    del open_orders[p["order_id"]]
                else:
                    open_orders[p["order_id"]] = replace(existing, qty=remaining)

        elif event.type == ev.MATCH_PROPOSED:
            matches[p["match_id"]] = Match(
                match_id=p["match_id"],
                bid_order_id=p["bid_order_id"],
                ask_order_id=p["ask_order_id"],
                clearing_price=p["clearing_price"],
                qty=p["qty"],
                score=p["score"],
                rationale=p["rationale"],
            )

        elif event.type == ev.CREDITS_TRANSFERRED:
            balances[p["from_actor_id"]] -= p["amount"]
            balances[p["to_actor_id"]] += p["amount"]

        elif event.type == ev.POINTS_MINTED:
            # The only event that increases the total supply. A transfer moves
            # points between two balances and nets to zero; a mint credits one
            # balance from nowhere, which is why the accountant is the only
            # actor allowed to write one and why `assert_invariants` checks
            # that it was.
            balances[p["actor_id"]] += p["points"]

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
            existing = settlements.get(p["settlement_id"])
            if existing is None:
                # A completion whose initiation is missing. Nothing reachable
                # writes one today — both rails initiate first and
                # `Accountant.repair` looks the initiation up before writing —
                # but this used to raise KeyError, and the log is append-only
                # and enforced by triggers. One malformed event would have made
                # `fold()`, and therefore every read of exchange state, raise
                # forever on a database that by design cannot be mended: the
                # audit trail becomes unreadable, which is the one failure this
                # project cannot survive. SETTLEMENT_FAILED twelve lines below
                # has always handled the same case gracefully.
                #
                # The values here are UNKNOWN, NOT ZERO: the completion payload
                # carries only the settlement and payment ids, so there is no
                # match, currency or amount to recover. Nothing may read this
                # record as a settled exposure — `_spend_to_date` counts from
                # SETTLEMENT_INITIATED in the log rather than from here, and
                # `HouseAgent.observe` skips a completion with no initiation —
                # and `assert_invariants` reports it as `orphaned_completion`
                # so it is named rather than merely survived.
                settlements[p["settlement_id"]] = Settlement(
                    settlement_id=p["settlement_id"],
                    match_id=p.get("match_id", ""),
                    currency=Currency(p.get("currency", Currency.INR)),
                    amount=p.get("amount", 0),
                    status=SettlementStatus.COMPLETED,
                    razorpay_payment_id=p.get("razorpay_payment_id"),
                )
            else:
                settlements[p["settlement_id"]] = replace(
                    existing,
                    status=SettlementStatus.COMPLETED,
                    razorpay_payment_id=p.get("razorpay_payment_id"),
                )

        elif event.type == ev.SETTLEMENT_FAILED:
            existing = settlements.get(p["settlement_id"])
            if existing is None:
                # A settlement can fail before it is ever initiated — the INR
                # rail cannot create the Razorpay order, the CREDITS rail finds
                # the balance short. The failure payload is self-describing so
                # the outcome still projects rather than crashing the fold.
                settlements[p["settlement_id"]] = Settlement(
                    settlement_id=p["settlement_id"],
                    match_id=p["match_id"],
                    currency=Currency(p["currency"]),
                    amount=p["amount"],
                    status=SettlementStatus.FAILED,
                )
            else:
                settlements[p["settlement_id"]] = replace(
                    existing, status=SettlementStatus.FAILED
                )

        elif event.type == ev.ACTOR_FROZEN:
            existing = actors.get(p["actor_id"])
            if existing is not None:
                actors[p["actor_id"]] = replace(existing, status=ActorStatus.FROZEN)

        elif event.type == ev.ACTOR_RESUMED:
            existing = actors.get(p["actor_id"])
            if existing is not None:
                actors[p["actor_id"]] = replace(existing, status=ActorStatus.ACTIVE)

    return ExchangeState(
        actors=actors,
        assets=assets,
        open_orders=open_orders,
        credit_balances=dict(balances),
        settlements=settlements,
        matches=matches,
        event_offset=offset,
    )
