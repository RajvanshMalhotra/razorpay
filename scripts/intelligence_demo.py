"""Mint a lot from real settled activity, auction it, and show the trail.

  LLM_PROVIDER=ollama .venv/bin/python scripts/intelligence_demo.py

Reads whatever `runs/brokers.db` already holds, so run the broker demo first
for real settled trades to mine. No Razorpay call is made: brokers value the
lot in their own words and points move on the CREDITS ledger only.

Every point that moves here goes through `Exchange.execute_match` — the same
chokepoint the rupee trades use — so the gate fires, the balance is checked,
and the accountant's invariants cover the points economy rather than stopping
at the edge of it.
"""
from __future__ import annotations

import re
import sys

from dotenv import load_dotenv

from exchange.agents.broker import Broker
from exchange.eventlog import EventLog
from exchange.house.accountant import Accountant
from exchange.house.agent import HouseAgent
from exchange.house.auction import pay_royalties, run_auction, settle_purchase
from exchange.house.insights import HOUSE_ACTOR_ID
from exchange.ids import new_id
from exchange.llm.openai_compat import providers_from_env
from exchange.projections import fold
from exchange.rails.base import InsufficientCredits
from exchange.rails.credits import CreditRail
from exchange.service import Exchange

# Three merchants bidding on the same lot, each capped at what it is willing
# to risk on intel this quarter. The cap only bounds the bid — the LLM
# decides the actual number, and says why, in its own words.
BIDDERS = [
    ("m_buyer", 1850, "spends heavily in exactly this category"),
    ("m_rival", 1200, "watches this category but it isn't core"),
    ("m_quiet", 800, "small player here this quarter"),
]


def _balances(log: EventLog, actor_ids: list[str]) -> dict[str, int]:
    state = fold(log.read_all())
    return {a: state.credit_balances.get(a, 0) for a in actor_ids}


def _grant(accountant: Accountant, actor_id: str, points: int, correlation_id: str) -> None:
    """Give a merchant an opening balance, through the accountant.

    This used to be a raw `log.append(CREDITS_TRANSFERRED)` from the house —
    points conjured from an actor the auditor was configured to ignore. It is
    a POINTS_MINTED written by the sole minter now: capped, logged with its
    reason, and visible to the conservation check like everything else. The
    house gets nothing here; its balance comes from what it sells.
    """
    accountant.mint(
        actor_id, points, source_settlement_id=None,
        correlation_id=correlation_id,
        reason="opening balance: stands in for points earned before this log began",
    )


def main() -> int:
    load_dotenv()
    strong, _fast = providers_from_env()
    correlation_id = new_id("corr")
    print(f"Correlation id: {correlation_id}\n")

    log = EventLog("runs/brokers.db")
    house = HouseAgent(log, strong)
    accountant = Accountant(log, None)
    # No INR rail and no index: this flow only ever touches the points ledger,
    # and a rail that is never reached cannot make a network call.
    exchange = Exchange(log, index=None, inr_rail=None, credit_rail=CreditRail(log))

    observations = house.observe()
    print(f"=== WHAT RAZORPAY CAN SEE ===\n  {len(observations)} settled trades "
          f"across {len({o['actor_id'] for o in observations})} merchants\n")

    lot = house.mint_from(observations, correlation_id)
    if lot is None:
        refused = [e for e in log.read_by_correlation(correlation_id)
                   if e.type == "PRIVACY_REFUSED"][0]
        print("=== PRIVACY FLOOR ===")
        print(f"  REFUSED: {refused.payload['reason']}")
        print("  This is the floor working. Run more trades and try again.\n")
        log.close()
        return 0

    print(f"=== VICTORY FEED (free) ===\n  {lot.spec['headline']}\n")

    print("=== BROKERS VALUE THE LOT (points, sealed) ===")
    bids = []
    for actor_id, cap, persona in BIDDERS:
        _grant(accountant, actor_id, cap, f"seed_{correlation_id}")
        broker = Broker(actor_id, exchange, strong)
        bid = broker.value_insight(lot.spec["headline"], lot.spec["category"], cap)
        bids.append(bid)
        # The amount is already shown; drop the leading "BID: N" so what
        # prints is the reasoning itself, in the broker's own words.
        reasoning = re.sub(r"(?i)^\s*BID:\s*\d+\s*", "", bid.reason).strip()
        print(f"  {actor_id} ({persona}, cap {cap}):")
        if bid.parsed:
            print(f"    bids {bid.amount} — {reasoning}\n")
        else:
            # Not a bid of zero. An absent bid, on the record as one.
            print(f"    NO READABLE BID — said: {reasoning}\n")

    bidder_ids = [b.actor_id for b in bids]
    watched = bidder_ids + [HOUSE_ACTOR_ID]
    before = _balances(log, watched)

    print("=== AUCTION (second price) ===")
    result = run_auction(log, lot.asset_id, bids, correlation_id=correlation_id)
    if result.winner_id is None:
        print(f"  no clearing: {result.reason}")
        print("  Not an error — an outcome. Nothing is sold and nothing moves.\n")
        _books(log)
        _trail(log, correlation_id, collapse_from=None)
        log.close()
        return 0

    winning_bid = next(b.amount for b in bids if b.actor_id == result.winner_id)
    print(f"  winner: {result.winner_id}  bid {winning_bid}, pays {result.price}"
          f"  (second price — saved {winning_bid - result.price} vs its own bid)")
    print(f"  {result.reason}\n")

    print("=== THE GATE (points are money: they convert to fee rebates) ===")
    try:
        decision, settlement = settle_purchase(
            exchange, lot.asset_id, result, correlation_id=correlation_id,
        )
    except InsufficientCredits as exc:
        # Recorded, not swallowed: SETTLEMENT_FAILED is already in the log.
        print(f"  REFUSED at the rail: {exc}")
        print("  The winner keeps its points and the lot is not delivered.\n")
        _books(log)
        _trail(log, correlation_id, collapse_from=None)
        log.close()
        return 0

    print(f"  {decision.verdict}: {decision.reason}")
    print(f"  limits weighed: {decision.limits_evaluated}")
    if settlement is None:
        print("  No settlement. The gate refused before any point moved.\n")
        _books(log)
        _trail(log, correlation_id, collapse_from=None)
        log.close()
        return 0
    print(f"  settled {settlement.settlement_id}: {result.winner_id} pays "
          f"{HOUSE_ACTOR_ID} {settlement.amount} points\n")

    print("=== THE PAYOUT (their own wins earning them points) ===")
    contributors = lot.spec["contributor_ids"]
    royalty_starts = len(log.read_by_correlation(correlation_id))
    per_contributor, paid = pay_royalties(
        exchange, lot.asset_id, contributors, result.price,
        correlation_id=correlation_id,
    )
    print(f"  {HOUSE_ACTOR_ID} pays {paid} of {len(contributors)} contributors "
          f"{per_contributor} points each ({per_contributor * paid} total)")
    print(f"  the house keeps {result.price - per_contributor * paid} — the "
          f"residual of a 30% share, rounded its way\n")

    after = _balances(log, watched)
    print("  points balances, before -> after:")
    for actor_id in watched:
        print(f"    {actor_id:<10} {before[actor_id]:>6} -> {after[actor_id]:>6}")
    print()

    _books(log)
    _trail(log, correlation_id, collapse_from=royalty_starts)
    log.close()
    return 0


def _books(log: EventLog) -> None:
    print("=== THE BOOKS ===")
    violations = Accountant(log, None).assert_invariants()
    print(f"  invariant violations: {len(violations)}")
    for v in violations:
        print(f"    {v.kind}: {v.detail}")
    print()


def _trail(log: EventLog, correlation_id: str, collapse_from: int | None) -> None:
    """Print the story on one id.

    The royalty distribution is one gated settlement per contributor, and the
    privacy floor guarantees at least 25 of them, so past that point the trail
    is summarised by type rather than printed line by line. Nothing is hidden:
    the counts are what a reader would otherwise tally by hand.
    """
    events = log.read_by_correlation(correlation_id)
    head = events if collapse_from is None else events[:collapse_from]
    tail = [] if collapse_from is None else events[collapse_from:]

    print("=== AUDIT TRAIL ===")
    for event in head:
        print(f"  [{event.seq:>3}] {event.actor_id:<12} {event.type}")

    if tail:
        counts: dict[str, int] = {}
        for event in tail:
            counts[event.type] = counts.get(event.type, 0) + 1
        print(f"  ... royalty distribution, {len(tail)} events "
              f"[{tail[0].seq}-{tail[-1].seq}]:")
        for event_type, count in counts.items():
            print(f"        {event_type} x{count}")


if __name__ == "__main__":
    sys.exit(main())
