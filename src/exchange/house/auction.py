"""Sealed-bid, second-price. Highest bidder wins and pays the runner-up's bid.

Why second-price: under first-price no agent ever bids what a lot is worth to
it — it shades down, and how far depends on guessing rivals, so the reasoning
in the log becomes mind-reading rather than valuation. Here a bid decides
WHETHER you win but not WHAT you pay, so bidding true value is optimal and the
sentence that lands in the audit trail is about worth.
"""
from __future__ import annotations

from dataclasses import dataclass

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.insights import HOUSE_ACTOR_ID


@dataclass(frozen=True)
class Bid:
    actor_id: str
    amount: int
    reason: str


@dataclass(frozen=True)
class Clearing:
    winner_id: str | None
    price: int | None
    reason: str


def clear(bids: list[Bid]) -> Clearing:
    """Resolve a sealed auction.

    Fewer than two bids does not clear, and that is correct rather than an
    error: second-price needs a second price, and a market of one has none.
    """
    if len(bids) < 2:
        if len(bids) == 0:
            return Clearing(None, None, "no bids; a market of one has no price")
        else:
            return Clearing(None, None, "only one bid; a market of one has no price")

    ranked = sorted(bids, key=lambda b: b.amount, reverse=True)
    return Clearing(ranked[0].actor_id, ranked[1].amount, "cleared at the second price")


def run_auction(
    log: EventLog,
    asset_id: str,
    bids: list[Bid],
    correlation_id: str,
) -> Clearing:
    """Open, record every bid with its reasoning, and clear — all on one id."""
    log.append(HOUSE_ACTOR_ID, ev.AUCTION_OPENED,
               {"asset_id": asset_id}, correlation_id=correlation_id)

    for bid in bids:
        log.append(bid.actor_id, ev.BID_PLACED,
                   {"asset_id": asset_id, "amount": bid.amount, "reason": bid.reason},
                   correlation_id=correlation_id)

    result = clear(bids)
    log.append(HOUSE_ACTOR_ID, ev.AUCTION_CLEARED,
               {"asset_id": asset_id, "winner_id": result.winner_id,
                "price": result.price, "reason": result.reason},
               correlation_id=correlation_id)
    return result
