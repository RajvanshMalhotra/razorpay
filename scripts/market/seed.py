"""Put the roster into the exchange, and be safe to run twice.

RESUMABILITY IS THE WHOLE JOB. A two-hour market run against a persistent log
will be interrupted — this project has already lost three agents to session
limits mid-task — and the operator's instinct on restart is to run the same
command again. That has to be free.

The log already knows who is registered and what is listed, so nothing here
needs a flag or a marker file: fold it and skip what is already there. A
doubled book is not a cosmetic problem either. Every ask would have a twin at
the same price from the same seller, so `choose()` would spend a model call
deciding between two copies of one merchant, and the trace would read as if
the market were twice its real size.
"""
from __future__ import annotations

from dataclasses import dataclass

from exchange.ids import new_id
from exchange.models import (
    Actor,
    ActorKind,
    Asset,
    AssetKind,
    Currency,
    Order,
    Side,
)

# Far enough out that nothing expires mid-run, and stable so a resumed run
# does not post a differently-dated twin of an order it already placed.
ASK_EXPIRES_AT = "2026-12-31T00:00:00+00:00"


@dataclass(frozen=True)
class SeedReport:
    registered: int = 0
    listed: int = 0
    asks_posted: int = 0
    skipped_actors: int = 0
    skipped_assets: int = 0

    def __str__(self) -> str:
        return (
            f"registered {self.registered} (skipped {self.skipped_actors}), "
            f"listed {self.listed} (skipped {self.skipped_assets}), "
            f"posted {self.asks_posted} asks"
        )


def seed(exchange, merchants) -> SeedReport:
    """Register merchants, list what they sell, and post their opening asks.

    Idempotent by reading the projection rather than by tracking state of its
    own: a marker file can disagree with the log, and the log is the authority.
    """
    state = exchange.state()
    known_actors = set(state.actors)
    known_assets = set(state.assets)
    # Asks already on the book, by the asset they are for. An ask that has been
    # filled or expired is deliberately NOT counted: the merchant is entitled
    # to re-post it in a later round, and only an unfilled duplicate is a bug.
    open_ask_assets = {
        order.asset_ref
        for order in state.open_orders.values()
        if order.side == Side.ASK and order.asset_ref
    }

    registered = listed = posted = skipped_actors = skipped_assets = 0

    for merchant in merchants:
        if merchant.actor_id in known_actors:
            skipped_actors += 1
        else:
            exchange.register_actor(Actor(
                actor_id=merchant.actor_id, kind=ActorKind.MERCHANT,
                brief=merchant.mandate_input(),
            ))
            registered += 1

        for listing in merchant.sells:
            if listing.asset_id in known_assets:
                skipped_assets += 1
            else:
                exchange.list_asset(Asset(
                    asset_id=listing.asset_id,
                    kind=AssetKind.GOODS,
                    title=listing.title,
                    spec=listing.spec,
                    currency=Currency.INR,
                    origin_actor_id=merchant.actor_id,
                ))
                listed += 1

            if listing.asset_id in open_ask_assets:
                continue
            exchange.post_order(
                Order(
                    order_id=new_id("ord"),
                    actor_id=merchant.actor_id,
                    side=Side.ASK,
                    asset_ref=listing.asset_id,
                    asset_query=None,
                    qty=listing.qty,
                    limit_price=listing.ask_price,
                    currency=Currency.INR,
                    expires_at=ASK_EXPIRES_AT,
                    policy_snapshot={},
                ),
                correlation_id=f"seed_{merchant.actor_id}",
            )
            posted += 1

    return SeedReport(
        registered=registered,
        listed=listed,
        asks_posted=posted,
        skipped_actors=skipped_actors,
        skipped_assets=skipped_assets,
    )
