"""Mint a lot from real settled activity, auction it, and show the trail.

  LLM_PROVIDER=ollama .venv/bin/python scripts/intelligence_demo.py

Reads whatever `runs/brokers.db` already holds, so run the broker demo first
for real settled trades to mine. No Razorpay call is made: brokers value the
lot in their own words and points move on the CREDITS ledger only.
"""
from __future__ import annotations

import re
import sys

from dotenv import load_dotenv

from exchange import events as ev
from exchange.agents.broker import Broker
from exchange.eventlog import EventLog
from exchange.house.accountant import Accountant
from exchange.house.agent import HouseAgent
from exchange.house.auction import run_auction
from exchange.house.insights import HOUSE_ACTOR_ID
from exchange.house.points import royalty_for
from exchange.ids import new_id
from exchange.llm.openai_compat import providers_from_env
from exchange.projections import fold

# Three merchants bidding on the same lot, each capped at what it is willing
# to risk on intel this quarter. The cap only bounds the bid — the LLM
# decides the actual number, and says why, in its own words.
BIDDERS = [
    ("m_buyer", 1850, "spends heavily in exactly this category"),
    ("m_rival", 1200, "watches this category but it isn't core"),
    ("m_quiet", 800, "small player here this quarter"),
]


class _LogOnlyExchange:
    """Just enough for `Broker.__init__` — no order book, no rail, no
    network call. `value_insight` never touches anything but the log."""

    def __init__(self, log: EventLog) -> None:
        self.log = log


def _balances(log: EventLog, actor_ids: list[str]) -> dict[str, int]:
    state = fold(log.read_all())
    return {a: state.credit_balances.get(a, 0) for a in actor_ids}


def _mint_points(log: EventLog, actor_id: str, amount: int, correlation_id: str) -> None:
    """Seed a merchant's points balance, standing in for points it already
    earned trading well before this run started."""
    log.append(HOUSE_ACTOR_ID, ev.CREDITS_TRANSFERRED,
               {"from_actor_id": HOUSE_ACTOR_ID, "to_actor_id": actor_id,
                "amount": amount},
               correlation_id=correlation_id)


def _pay_points(log: EventLog, from_actor: str, to_actor: str, amount: int,
                correlation_id: str) -> None:
    log.append(from_actor, ev.CREDITS_TRANSFERRED,
               {"from_actor_id": from_actor, "to_actor_id": to_actor, "amount": amount},
               correlation_id=correlation_id)


def main() -> int:
    load_dotenv()
    strong, _fast = providers_from_env()
    correlation_id = new_id("corr")
    print(f"Correlation id: {correlation_id}\n")

    log = EventLog("runs/brokers.db")
    house = HouseAgent(log, strong)

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
    exchange_stub = _LogOnlyExchange(log)
    bids = []
    for actor_id, cap, persona in BIDDERS:
        _mint_points(log, actor_id, cap, f"seed_{correlation_id}")
        broker = Broker(actor_id, exchange_stub, strong)
        bid = broker.value_insight(lot.spec["headline"], lot.spec["category"], cap)
        bids.append(bid)
        # The amount is already shown; drop the leading "BID: N" so what
        # prints is the reasoning itself, in the broker's own words.
        reasoning = re.sub(r"(?i)^\s*BID:\s*\d+\s*", "", bid.reason).strip()
        print(f"  {actor_id} ({persona}, cap {cap}):")
        print(f"    bids {bid.amount} — {reasoning}\n")

    bidder_ids = [b.actor_id for b in bids]
    before = _balances(log, bidder_ids + [HOUSE_ACTOR_ID])

    print("=== AUCTION (second price) ===")
    result = run_auction(log, lot.asset_id, bids, correlation_id=correlation_id)
    winning_bid = next(b.amount for b in bids if b.actor_id == result.winner_id)
    print(f"  winner: {result.winner_id}  bid {winning_bid}, pays {result.price}"
          f"  (second price — saved {winning_bid - result.price} vs its own bid)\n")

    print("=== THE PAYOUT ===")
    contributors = lot.spec["contributor_ids"]
    per_contributor = royalty_for(result.price, len(contributors))

    _pay_points(log, result.winner_id, HOUSE_ACTOR_ID, result.price, correlation_id)
    print(f"  {result.winner_id} pays {HOUSE_ACTOR_ID}: {result.price} points")

    if per_contributor > 0:
        for contributor_id in contributors:
            _pay_points(log, HOUSE_ACTOR_ID, contributor_id, per_contributor, correlation_id)
    print(f"  {HOUSE_ACTOR_ID} pays each of {len(contributors)} contributors: "
          f"{per_contributor} points ({per_contributor * len(contributors)} total — "
          f"their own wins earning them points)\n")

    after = _balances(log, bidder_ids + [HOUSE_ACTOR_ID])
    print("  points balances, before -> after:")
    for actor_id in bidder_ids + [HOUSE_ACTOR_ID]:
        print(f"    {actor_id:<10} {before[actor_id]:>6} -> {after[actor_id]:>6}")
    print()

    print("=== THE BOOKS ===")
    violations = Accountant(log, None).assert_invariants()
    print(f"  invariant violations: {len(violations)}")
    for v in violations:
        print(f"    {v.kind}: {v.detail}")
    print()

    print("=== AUDIT TRAIL ===")
    for event in log.read_by_correlation(correlation_id):
        print(f"  [{event.seq:>3}] {event.actor_id:<12} {event.type}")

    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
