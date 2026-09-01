"""Mine the market, auction what you find, pay the merchants who made it.

SEPARATELY RUNNABLE FROM THE TRADING, deliberately. Tuning the intelligence
economy means running this repeatedly against one log, and re-trading the
market each time would be slow, expensive, and would change the very data the
last run was tuned against. So this reads settled activity and writes
intelligence; it never trades.

THE PRIVACY REFUSAL IS A FEATURE OF THE OUTPUT, not an error path. The floor
needs 25 distinct contributing merchants, and a run that has not reached it
must say so plainly rather than crash — a floor nobody can see is
indistinguishable from no floor, and a refusal that reads as a working control
is a good thing to have on camera.

The order matters and is the whole shape of the product:

    observe  -> what actually settled, per merchant
    read     -> the campaign board, which is what the lot is made of
    mint     -> a headline, if the floor allows it
    value    -> each broker prices the lot in its own words
    auction  -> sealed bids, second price
    settle   -> the winner pays, THROUGH THE GATE
    royalty  -> contributors are paid out of what the house took in
"""
from __future__ import annotations

from dataclasses import dataclass, field

from exchange import events as ev
from exchange.house.auction import pay_royalties, run_auction, settle_purchase
from exchange.house.campaigns import is_board_row
from exchange.house.insights import HOUSE_ACTOR_ID


@dataclass
class CycleReport:
    observations: int = 0
    contributors: int = 0
    minted: bool = False
    board_rows: int = 0
    refused_reason: str | None = None
    headline: str | None = None
    bids: tuple = ()
    winner_id: str | None = None
    clearing_price: int | None = None
    royalty_each: int = 0
    royalties_paid: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        if self.refused_reason:
            return (f"refused: {self.refused_reason} "
                    f"({self.contributors} contributors, floor needs 25)")
        if not self.minted:
            return f"nothing minted from {self.observations} observations"
        won = (f"{self.winner_id} paid {self.clearing_price}"
               if self.winner_id else "no winner")
        return (f"minted {self.headline!r}; {len(self.bids)} bids; {won}; "
                f"{self.royalties_paid} royalties of {self.royalty_each}")


def run_house_cycle(
    exchange,
    house,
    brokers,
    correlation_id: str,
    bid_cap: int = 2_000,
) -> CycleReport:
    """One pass: observe, mint, auction, settle, pay. Never raises."""
    report = CycleReport()

    observations = house.observe()
    report.observations = len(observations)
    report.contributors = len({o["actor_id"] for o in observations})

    # The board, if one has been published. This is what makes the auction a
    # market rather than a ritual: without it the winner buys a trade count
    # and a total, and no business would part with points for that.
    board = [e.payload for e in exchange.log.read_all()
             if e.type == ev.CAMPAIGN_RANKED and is_board_row(e)]
    board.sort(key=lambda row: row["rank"])
    report.board_rows = len(board)

    lot = house.mint_from(observations, correlation_id=correlation_id,
                          board=board or None)
    if lot is None:
        # `mint_from` already logged PRIVACY_REFUSED with the reason and k.
        report.refused_reason = (
            f"derived from {report.contributors} distinct merchants"
        )
        return report

    report.minted = True
    report.headline = lot.spec.get("headline")
    category = lot.spec.get("category", "trade")

    # Every broker but the contributors' own house prices the lot itself. An
    # unreadable reply is an ABSENT bid, never a bid of zero — otherwise two
    # bad replies let a third win at a price of nothing while the log records
    # "cleared at the second price".
    bids = []
    for actor_id, broker in brokers.items():
        if actor_id == HOUSE_ACTOR_ID:
            continue
        try:
            bids.append(broker.value_insight(report.headline, category, bid_cap))
        except Exception as exc:  # noqa: BLE001 - one bad valuation is not the auction
            report.errors.append(f"{actor_id}: {type(exc).__name__}: {exc}")
    report.bids = tuple(bids)

    clearing = run_auction(exchange.log, lot.asset_id, bids,
                           correlation_id=correlation_id)
    report.winner_id = clearing.winner_id
    report.clearing_price = clearing.price
    if clearing.winner_id is None:
        return report

    try:
        settle_purchase(exchange, lot.asset_id, clearing,
                        correlation_id=correlation_id)
    except Exception as exc:  # noqa: BLE001 - recorded by the rail before it raised
        report.errors.append(f"purchase: {type(exc).__name__}: {exc}")
        return report

    # Royalties come out of what the house just took in, one gated payout each.
    # The credit rail refuses a payout the house cannot fund, which is what
    # makes the house's balance real rather than an exemption.
    try:
        each, paid = pay_royalties(
            exchange,
            lot.asset_id,
            lot.spec.get("contributor_ids", ()),
            clearing.price,
            correlation_id=correlation_id,
        )
        report.royalty_each, report.royalties_paid = each, paid
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"royalties: {type(exc).__name__}: {exc}")

    return report
