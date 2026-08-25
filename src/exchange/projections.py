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

    # --- fields the gate reads, so the gate never re-scans the log ----------
    #
    # Everything below is DERIVED HERE, from the log, and from nothing else.
    # Each replaced a full-log scan inside `Exchange`, and each had to keep the
    # property that made the scan trustworthy: the checker computes the figure
    # itself rather than being handed one. A projection is still a derivation —
    # what changed is how often it is recomputed, not who computes it.

    # Every order ever posted, as posted, keyed by order_id. NOT the book:
    # `open_orders` loses an order when it fills or expires, and the mint basis
    # has to be readable exactly then (`Exchange._counterparty_ask_price`).
    # Most recent ORDER_POSTED for an id wins, the same resolution `open_orders`
    # gives a repeated id, so the book and the mint basis can never disagree
    # about the same order.
    posted_orders: dict[str, Order] = field(default_factory=dict)

    # Committed spend, WITH THE TIME IT WAS COMMITTED:
    # actor_id -> currency -> ((ts, minor units), ...), oldest first.
    #
    # Counted at SETTLEMENT_INITIATED, against the actor that initiated it —
    # money is committed the moment the exposure is opened, and a PENDING
    # settlement is exactly what a cap is meant to bound.
    #
    # A RUNNING TOTAL CANNOT BE WINDOWED, which is why this is a ledger rather
    # than the scalar it used to be. The spec always called for a rolling
    # window; the scalar made the cap cumulative-for-life, so on one persistent
    # log re-run for tuning a merchant got ~25 typical trades EVER and every
    # trade after that returned a correct DENY that read like a gate bug.
    #
    # `ts` is the EVENT's timestamp, stamped by `EventLog.append`. Nothing a
    # caller passes reaches it: the party the cap constrains supplies neither
    # the amount (it comes off the settlement the rail wrote) nor the clock.
    spend_ledger: dict[str, dict[str, tuple[tuple[str, int], ...]]] = field(
        default_factory=dict
    )

    # Every action_ref that has already carried a POLICY_DECIDED. This backs a
    # CORRECTNESS check, not a convenience one: `assert_invariants` joins
    # settlements to decisions on match_id, and that join is only sound while a
    # match_id reaches the gate at most once.
    decided_action_refs: frozenset[str] = field(default_factory=frozenset)

    event_offset: int = 0

    @property
    def spend_to_date(self) -> dict[str, dict[str, int]]:
        """Lifetime committed spend per actor per currency, summed from the ledger.

        A DERIVED VIEW, not a second source of truth — it is computed from
        `spend_ledger` on every read, so it cannot drift from it, and it is not
        a dataclass field so it plays no part in the equality the accountant's
        `projection_drift` check rests on.

        Reporting only. THE GATE DOES NOT READ THIS: a lifetime figure is not
        the bound the spec asks for. See `Exchange._spend_to_date`.
        """
        return {
            actor: {
                currency: sum(amount for _, amount in entries)
                for currency, entries in by_currency.items()
            }
            for actor, by_currency in self.spend_ledger.items()
        }


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
    posted_orders: dict[str, Order] = dict(state.posted_orders)
    # Shallow: the per-currency values are immutable tuples, so only the two
    # dict levels are copied. Extending one costs a tuple concatenation, and
    # only on a settlement — the same shape the running total had.
    spend: dict[str, dict[str, tuple[tuple[str, int], ...]]] = {
        actor: dict(by_currency) for actor, by_currency in state.spend_ledger.items()
    }
    decided: set[str] = set(state.decided_action_refs)
    offset = state.event_offset

    for event in events:
        offset = max(offset, event.seq)
        p = event.payload

        if event.type == ev.ACTOR_REGISTERED:
            # A freeze already on record SURVIVES a later registration. Only an
            # ACTOR_RESUMED lifts a freeze; if registering could, then the
            # containment for an unbacked completion — which the accountant
            # applies whether or not the actor ever registered — would be
            # undone by the frozen merchant filling in its own paperwork. The
            # party being contained must not be able to supply the fact that
            # ends the containment.
            existing = actors.get(p["actor_id"])
            frozen = existing is not None and existing.status == ActorStatus.FROZEN
            actors[p["actor_id"]] = Actor(
                actor_id=p["actor_id"],
                kind=ActorKind(p["kind"]),
                merchant_id=p.get("merchant_id"),
                plan_tier=p.get("plan_tier", "standard"),
                status=(
                    ActorStatus.FROZEN if frozen
                    else ActorStatus(p.get("status", "ACTIVE"))
                ),
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
            order = Order(
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
            open_orders[p["order_id"]] = order
            # The book loses this record on a fill or an expiry; this one keeps
            # it. Nothing removes from `posted_orders` — what was asked stays
            # readable for as long as the log does.
            posted_orders[p["order_id"]] = order

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

        elif event.type == ev.POLICY_DECIDED:
            # Membership only — the verdict is deliberately NOT projected here.
            # `Exchange._already_decided` refuses a second trip through the gate
            # whatever the first verdict was, and a projection that carried the
            # verdict would invite a caller to branch on it and re-run the ones
            # that were denied. `action_ref` is read defensively because a fold
            # that raises on a malformed row makes an append-only log
            # permanently unreadable.
            action_ref = p.get("action_ref")
            if action_ref is not None:
                decided.add(action_ref)

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
            # Exposure is committed here, so this is where the cap's usage
            # figure accrues — against the actor that INITIATED it (the event's
            # actor, which both rails set to the payer), on the currency the
            # payload names, AT THE TIME THE ENVELOPE RECORDS. Read only from
            # the payload and the envelope: no key beyond the ones this branch
            # already requires, so the fold gains no new way to raise.
            spend.setdefault(event.actor_id, {})
            spend[event.actor_id][p["currency"]] = (
                spend[event.actor_id].get(p["currency"], ())
                + ((event.ts, p["amount"]),)
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
            # A FREEZE BINDS WHATEVER THE REGISTRATION ORDER. This used to do
            # nothing at all for an actor with no ACTOR_REGISTERED — and
            # `execute_match` requires no registration, so exactly the actor
            # that could never be stopped was the one that could still trade.
            # `Accountant._contain_unbacked` is not advisory: it is the only
            # containment there is for a completion the remote denies, which
            # cannot be repaired by anything in this system. A containment that
            # silently no-ops for an unknown actor is not one.
            #
            # So an unknown actor is CREATED here, in FROZEN state, with kind
            # UNKNOWN. The freeze is a fact the log holds; the kind is not, and
            # is not invented. A later ACTOR_REGISTERED fills the kind in and
            # leaves the freeze standing (see ACTOR_REGISTERED above).
            existing = actors.get(p["actor_id"])
            if existing is None:
                actors[p["actor_id"]] = Actor(
                    actor_id=p["actor_id"],
                    kind=ActorKind.UNKNOWN,
                    status=ActorStatus.FROZEN,
                )
            else:
                actors[p["actor_id"]] = replace(existing, status=ActorStatus.FROZEN)

        elif event.type == ev.ACTOR_RESUMED:
            # No symmetric creation here, deliberately. A freeze is a claim
            # that has to bind something, so it creates the record it binds; a
            # resume only lifts a freeze, and there is nothing to lift on an
            # actor with no record. Creating an ACTIVE record for one would let
            # a resume-before-freeze conjure an actor into existence, which is
            # the containment being undone by ordering.
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
        posted_orders=posted_orders,
        spend_ledger=spend,
        decided_action_refs=frozenset(decided),
        event_offset=offset,
    )
