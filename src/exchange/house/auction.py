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
from exchange.house.points import ROYALTY_SHARE_BPS, royalty_for
from exchange.ids import new_id
from exchange.models import (
    ActorStatus,
    Currency,
    Match,
    PolicyDecision,
    Settlement,
    SettlementStatus,
)
from exchange.policy import PolicyContext


@dataclass(frozen=True)
class Bid:
    actor_id: str
    amount: int
    reason: str
    parsed: bool = True
    """Whether `amount` is a valuation this bidder actually expressed.

    A reply the parser cannot read is not a bid of nothing — it is the absence
    of a bid, and the two must not be spelled the same way. `amount == 0` on
    an unparsed reply used to be indistinguishable from an agent that genuinely
    valued the lot at nothing, and it counted toward the two-bid minimum: two
    unreadable replies let the third bidder win at a price of zero while
    AUCTION_CLEARED called it "cleared at the second price".
    """


@dataclass(frozen=True)
class Clearing:
    winner_id: str | None
    price: int | None
    reason: str


def clear(bids: list[Bid]) -> Clearing:
    """Resolve a sealed auction.

    Fewer than two READABLE bids does not clear, and that is correct rather
    than an error: second-price needs a second price, and a market of one has
    none. An unreadable reply is still logged — the trail should show that the
    agent said something, and that it could not be used — but it cannot set a
    price, because there is no valuation in it to set one with.

    A genuine bid of zero is a different thing and still counts: valuing a lot
    at nothing is an opinion about worth, and under second price it is a safe
    one to express.
    """
    readable = [b for b in bids if b.parsed]
    unreadable = len(bids) - len(readable)

    if len(readable) < 2:
        if not readable:
            reason = "no bids; there is no market"
        else:
            reason = "only one bid; a market of one has no price"
        if unreadable:
            reason += (
                f" ({unreadable} of {len(bids)} replies could not be read as a bid)"
            )
        return Clearing(None, None, reason)

    ranked = sorted(readable, key=lambda b: b.amount, reverse=True)
    reason = "cleared at the second price"
    if unreadable:
        reason += f" ({unreadable} unreadable {'reply' if unreadable == 1 else 'replies'} excluded)"
    return Clearing(ranked[0].actor_id, ranked[1].amount, reason)


def run_auction(
    log: EventLog,
    asset_id: str,
    bids: list[Bid],
    correlation_id: str,
) -> Clearing:
    """Open, record every bid with its reasoning, and clear — all on one id.

    Every reply is logged, the unreadable ones included and marked as such.
    Dropping them here would hide the fact that an agent answered at all,
    which is the interesting thing about an unreadable answer.
    """
    log.append(HOUSE_ACTOR_ID, ev.AUCTION_OPENED,
               {"asset_id": asset_id}, correlation_id=correlation_id)

    for bid in bids:
        log.append(bid.actor_id, ev.BID_PLACED,
                   {"asset_id": asset_id, "amount": bid.amount, "reason": bid.reason,
                    "parsed": bid.parsed},
                   correlation_id=correlation_id)

    result = clear(bids)
    log.append(HOUSE_ACTOR_ID, ev.AUCTION_CLEARED,
               {"asset_id": asset_id, "winner_id": result.winner_id,
                "price": result.price, "reason": result.reason},
               correlation_id=correlation_id)
    return result


# The auction is not an order-book flow: the sealed bid IS the order, and it
# is already in the log as BID_PLACED. The Match handed to the gate therefore
# names pseudo-orders rather than pretending two ORDER_POSTED events exist —
# the prefix is there so a reader can tell at a glance that these refer to an
# auction and not to the book.
def _lot_match(asset_id: str, buyer_id: str, seller_id: str, price: int,
               rationale: str) -> Match:
    return Match(
        match_id=new_id("mch"),
        bid_order_id=f"auction:{asset_id}:{buyer_id}",
        ask_order_id=f"auction:{asset_id}:{seller_id}",
        clearing_price=price,
        qty=1,
        score=1.0,
        rationale=rationale,
    )


# The house has no relationship record with anyone, so the pessimistic value is
# the honest one. It is not a formality: at confidence 0 the unknown-counterparty
# cap binds, which is exactly the bound that should apply to a points transfer
# between two parties with no history.
_NO_HISTORY = PolicyContext(
    actor_status=ActorStatus.ACTIVE,   # discarded and re-derived by the gate
    rolling_spend=0,                   # discarded and re-derived by the gate
    counterparty_confidence=0.0,
)


def settle_purchase(
    exchange,
    asset_id: str,
    clearing: Clearing,
    correlation_id: str,
    seller_id: str = HOUSE_ACTOR_ID,
) -> tuple[PolicyDecision | None, Settlement | None]:
    """Charge the winner the clearing price, through the gate.

    Points convert to Razorpay fee rebates, so this is a money action by the
    project's own definition, and design decision 4 is unqualified: every
    money action emits a PolicyDecision before it happens. This used to be a
    raw `log.append(CREDITS_TRANSFERRED)` — the one flow built to showcase the
    gate was the only flow that did not fire it, and the winner's ability to
    pay was never checked, so a clearing price above its balance drove it
    negative and surfaced as a conservation violation against the *buyer*.

    Routing through `execute_match` buys all of it at once: the decision, the
    credit rail's balance check, a SETTLEMENT_INITIATED the accountant's
    `ungated_settlement` invariant can see, and a match_id the settlement
    joins to.

    An auction that did not clear settles nothing. `InsufficientCredits`
    propagates — the rail has already logged SETTLEMENT_FAILED naming the
    shortfall, and the caller must not carry on as if the lot were sold.
    """
    if clearing.winner_id is None or clearing.price is None:
        return None, None

    match = _lot_match(
        asset_id, clearing.winner_id, seller_id, clearing.price,
        rationale=(
            f"insight lot {asset_id} cleared to {clearing.winner_id} at "
            f"{clearing.price} points: {clearing.reason}"
        ),
    )
    return exchange.execute_match(
        match,
        buyer_id=clearing.winner_id,
        seller_id=seller_id,
        ctx=_NO_HISTORY,
        correlation_id=correlation_id,
        currency=Currency.CREDITS,
    )


def pay_royalties(
    exchange,
    asset_id: str,
    contributor_ids,
    clearing_price: int,
    correlation_id: str,
    payer_id: str = HOUSE_ACTOR_ID,
) -> tuple[int, int]:
    """Share the clearing price back to the merchants whose activity made the lot.

    Returns `(per_contributor, paid)`.

    Gated one payout at a time for the same reason the purchase is: a point
    leaving the house's balance is a money action. It also makes the house's
    balance real — the credit rail refuses a payout the house cannot fund, so
    the house can no longer distribute points it never received. The pool is
    bounded at ROYALTY_SHARE_BPS of the clearing price the house was just paid
    on this same correlation, and floor division means the residual is kept
    rather than created: the distribution can never exceed the revenue.

    A shortfall stops the distribution rather than raising: the rail has
    already logged SETTLEMENT_FAILED for the contributor that could not be
    paid, and abandoning the contributors already paid is not a repair.
    """
    from exchange.rails.base import InsufficientCredits

    contributors = sorted(set(contributor_ids))
    per_contributor = royalty_for(clearing_price, len(contributors))
    if per_contributor <= 0:
        return 0, 0

    paid = 0
    for contributor_id in contributors:
        match = _lot_match(
            asset_id, payer_id, contributor_id, per_contributor,
            rationale=(
                f"royalty on {asset_id}: {ROYALTY_SHARE_BPS} bps of "
                f"{clearing_price} split {len(contributors)} ways"
            ),
        )
        try:
            _decision, settlement = exchange.execute_match(
                match,
                buyer_id=payer_id,
                seller_id=contributor_id,
                ctx=_NO_HISTORY,
                correlation_id=correlation_id,
                currency=Currency.CREDITS,
            )
        except InsufficientCredits:
            break
        # A refused payout is not a paid one. The DENY is in the log with its
        # reason; counting it would report a distribution that did not happen.
        if settlement is not None and settlement.status == SettlementStatus.COMPLETED:
            paid += 1

    return per_contributor, paid
