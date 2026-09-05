"""Give every merchant a shelf a person would recognise as a business.

    .venv/bin/python -m scripts.market.stock                # everyone, to five
    .venv/bin/python -m scripts.market.stock --merchant m_sunrise
    .venv/bin/python -m scripts.market.stock --size 5 --dry-run

WHY. Most merchants listed exactly one thing, because the run only needed one
line each for the trade it was going to make. On screen that reads as a shop
with a single item in the window — nobody believes it, and the ask box has
almost nothing to search. A catalogue is also the thing an agent shopping here
reads, so a thin catalogue makes the retrieval look worse than it is.

WHAT IT ADDS. Items that belong to the business already there: a roastery gets
beans and filter papers, a packaging works gets cartons and tape, a fabric mill
gets thread and trims. Prices are anchored to what that merchant already
lists, so a shelf reads as one supplier's price list rather than a category
average — and each merchant is nudged a little off its neighbours, because
nine cafes with identical prices is not a market.

IDEMPOTENT. A merchant already at the target size is left alone, so running
this twice does not double anybody's shelf. It only ever adds.

EVERY ITEM IS A REAL ASK. Each one is an ASSET_LISTED plus an ORDER_POSTED on
the sell side — the same two events the seeded listings use — so anything
added here is genuinely buyable through the exchange, not decoration on a
page.
"""
from __future__ import annotations

import argparse
import sys

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.ids import new_id

# Extra lines by trade, in the order they are handed out. Each is
# (title, price relative to what the merchant already lists, quantity).
# The multiplier keeps a shelf internally consistent: a supplier that opens
# dear on its headline item is dear across its range.
SHELVES = {
    "coffee_supply": [
        ("whole bean espresso blend medium roast", 0.92, 400),
        ("single origin filter coffee beans washed", 1.15, 300),
        ("cold brew steeping bags catering size", 0.44, 900),
        ("decaffeinated arabica beans swiss water", 1.28, 200),
        ("paper filter papers commercial brewer", 0.09, 4000),
    ],
    "beverage": [
        ("chai concentrate spiced masala", 0.78, 500),
        ("bottled cold coffee ready to drink", 0.35, 1200),
        ("roasted bean retail packs 250g", 1.55, 400),
        ("single use paper cups and lids 250ml", 0.06, 9000),
        ("reusable steel tumblers branded", 2.10, 300),
    ],
    "packaging": [
        ("corrugated cartons double wall", 1.95, 3000),
        ("bubble wrap rolls air cushioned", 1.40, 1200),
        ("packing tape rolls hot melt", 0.55, 5000),
        ("void fill kraft paper rolls", 1.05, 1500),
        ("pallet stretch film cast", 2.30, 800),
    ],
    "textiles": [
        ("cotton twill fabric rolls dyed", 0.88, 500),
        ("garment care labels woven satin", 0.04, 20000),
        ("elastic tape rolls knitted", 0.11, 6000),
        ("polyester buttons moulded four hole", 0.02, 40000),
        ("garment poly bags self seal", 0.03, 25000),
    ],
    "electronics_accessories": [
        ("charging bricks twenty watt", 1.30, 900),
        ("cable ties nylon assorted", 0.03, 30000),
        ("phone stands aluminium folding", 0.75, 700),
        ("silicone protective cases clear", 0.42, 1800),
        ("packaging inserts moulded pulp", 0.15, 4000),
    ],
    "dry_goods": [
        ("turmeric powder ground bulk", 0.22, 800),
        ("chickpea pulses whole bulk", 0.14, 1500),
        ("refined sunflower oil tins", 0.31, 600),
        ("jaggery blocks unrefined", 0.19, 900),
        ("mustard seeds whole cleaned", 0.16, 1100),
    ],
}


def _existing(events, actor_id) -> set:
    """Titles this merchant already lists, so nothing is duplicated."""
    assets = {e.payload.get("asset_id"): e.payload.get("title", "")
              for e in events if e.type == ev.ASSET_LISTED}
    listed = set()
    for event in events:
        if (event.type == ev.ORDER_POSTED
                and event.actor_id == actor_id
                and event.payload.get("side") == "ASK"):
            title = assets.get(event.payload.get("asset_ref"))
            if title:
                listed.add(title)
    return listed


def _anchor(events, actor_id) -> int:
    """What this merchant already asks, in paise. The shelf is built around
    it so one supplier's prices hang together."""
    prices = [e.payload.get("limit_price") for e in events
              if e.type == ev.ORDER_POSTED and e.actor_id == actor_id
              and e.payload.get("side") == "ASK" and e.payload.get("limit_price")]
    return int(sum(prices) / len(prices)) if prices else 20000


def _category(actor_id, roster) -> str:
    known = roster.get(actor_id)
    if known:
        return known
    # A business that joined after the roster was written. Every one of them
    # so far has been a cafe, and a cafe's shelf is the safe guess — an empty
    # one is certainly wrong.
    return "beverage"


def stock(log, events, actor_id, roster, size, spread, dry_run=False) -> list:
    """Top this merchant's shelf up to `size`. Returns what was added."""
    have = _existing(events, actor_id)
    want = size - len(have)
    if want <= 0:
        return []

    anchor = _anchor(events, actor_id)
    shelf = SHELVES[_category(actor_id, roster)]
    added = []
    for title, factor, qty in shelf:
        if len(added) >= want:
            break
        if title in have:
            continue
        # Nudge each merchant off its neighbours, deterministically, so nine
        # cafes are not nine copies of the same price list.
        price = max(100, int(anchor * factor * (1 + spread)))
        asset_id = new_id("ast")
        corr = new_id("stock")
        if not dry_run:
            log.append(actor_id, ev.ASSET_LISTED, {
                "asset_id": asset_id,
                "kind": "GOODS",
                "title": title,
                "spec": {"unit": "unit"},
                "currency": "INR",
                "origin_actor_id": actor_id,
            }, correlation_id=corr)
            log.append(actor_id, ev.ORDER_POSTED, {
                "order_id": new_id("ord"),
                "actor_id": actor_id,
                "side": "ASK",
                "asset_ref": asset_id,
                "asset_query": None,
                "qty": qty,
                "limit_price": price,
                "currency": "INR",
                "expires_at": "2026-12-31T00:00:00+00:00",
                "policy_snapshot": {},
            }, correlation_id=corr)
        added.append((title, price, qty))
    return added


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Stock merchant catalogues.")
    parser.add_argument("--db", default="runs/market.db")
    parser.add_argument("--merchant", default=None, help="just this one")
    parser.add_argument("--size", type=int, default=5,
                        help="how many lines each merchant should list")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    from scripts.market.roster import MERCHANTS
    roster = {m.actor_id: m.category for m in MERCHANTS}

    log = EventLog(args.db)
    try:
        events = log.read_all()
        actors = sorted({e.actor_id for e in events
                         if e.actor_id.startswith("m_")})
        if args.merchant:
            actors = [args.merchant]

        total = 0
        for n, actor in enumerate(actors):
            spread = ((n % 7) - 3) * 0.035        # -10.5% .. +10.5%, stable
            added = stock(log, events, actor, roster, args.size, spread,
                          args.dry_run)
            if added:
                name = actor[2:].replace("_", " ")
                print(f"  {name}")
                for title, price, qty in added:
                    print(f"      {title[:46]:<48} ₹{price / 100:>8,.2f}  "
                          f"{qty:,} units")
                total += len(added)
    finally:
        log.close()

    verb = "would add" if args.dry_run else "added"
    print(f"\n  {verb} {total} listings across {len(actors)} merchants.")
    if not args.dry_run and total:
        print("  Rebuild the pages and every shelf has something on it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
