"""Withdraw a badly-named listing and put the same goods back under a name.

    .venv/bin/python -m scripts.market.relist --dry-run
    .venv/bin/python -m scripts.market.relist

WHY THIS EXISTS RATHER THAN AN EDIT. Nine cafes were seeded with a listing
called "flavour syrup bottles house blend 0", "…1", "…2" — the loop counter
that generated them leaking into the thing a customer reads. The generator is
fixed, but the log already holds those events and the log is append-only. It
is supposed to be: a record you can quietly correct is not a record.

So this does what the merchant would do. It expires the listing and posts the
goods again under a proper name, at the same price and the same quantity. Two
new events per listing, both true, and the old ones stay exactly where they
are. Anyone reading the trail afterwards sees a shop that renamed a product,
which is what happened.

WHAT IT WILL NOT DO. It only ever touches ASKs whose title matches a pattern
you pass, and it never invents a price or a quantity — both are carried across
from the listing being retired.
"""
from __future__ import annotations

import argparse
import re
import sys

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.ids import new_id

# The seeded names, and what each cafe actually sells. Keyed by the merchant
# so the flavours stay put across runs rather than shuffling with the roster.
RENAMES = {
    "m_bl_thirdwave": "vanilla bean syrup bottles",
    "m_bl_koramangala": "hazelnut syrup bottles",
    "m_bl_indiranagar": "salted caramel syrup bottles",
    "m_bl_whitefield": "cardamom syrup bottles",
    "m_bl_jayanagar": "cinnamon syrup bottles",
    "m_bl_hsr": "dark chocolate syrup bottles",
    "m_bl_malleswaram": "gingerbread syrup bottles",
    "m_bl_electronic_city": "toasted coconut syrup bottles",
    "m_bl_yelahanka": "rose syrup bottles",
}


def stale(events, pattern) -> list:
    """Open ASKs whose title matches, with everything needed to repost them.

    An order that has already been filled or expired is left alone: renaming
    something nobody can buy would add two events and change nothing.
    """
    titles = {e.payload.get("asset_id"): e.payload.get("title", "")
              for e in events if e.type == ev.ASSET_LISTED}
    gone = {e.payload.get("order_id") for e in events
            if e.type in (ev.ORDER_EXPIRED, ev.ORDER_FILLED)}

    out = []
    for event in events:
        if event.type != ev.ORDER_POSTED:
            continue
        payload = event.payload or {}
        if payload.get("side") != "ASK" or payload.get("order_id") in gone:
            continue
        title = titles.get(payload.get("asset_ref"), "")
        if not title or not pattern.search(title):
            continue
        replacement = RENAMES.get(event.actor_id)
        if not replacement or replacement == title:
            continue
        out.append({
            "actor": event.actor_id,
            "order_id": payload.get("order_id"),
            "was": title,
            "now": replacement,
            "price": payload.get("limit_price"),
            "qty": payload.get("qty"),
            "spec": {"flavour": replacement.split(" syrup")[0]},
        })
    return out


def relist(log, row) -> None:
    corr = new_id("relist")
    log.append(row["actor"], ev.ORDER_EXPIRED, {
        "order_id": row["order_id"],
        "reason": "relisted under its product name",
    }, correlation_id=corr)

    asset_id = new_id("ast")
    log.append(row["actor"], ev.ASSET_LISTED, {
        "asset_id": asset_id,
        "kind": "GOODS",
        "title": row["now"],
        "spec": row["spec"],
        "currency": "INR",
        "origin_actor_id": row["actor"],
    }, correlation_id=corr)
    log.append(row["actor"], ev.ORDER_POSTED, {
        "order_id": new_id("ord"),
        "actor_id": row["actor"],
        "side": "ASK",
        "asset_ref": asset_id,
        "asset_query": None,
        "qty": row["qty"],
        "limit_price": row["price"],
        "currency": "INR",
        "expires_at": "2026-12-31T00:00:00+00:00",
        "policy_snapshot": {},
    }, correlation_id=corr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Relist badly-named goods under a proper name.")
    parser.add_argument("--db", default="runs/market.db")
    parser.add_argument("--match", default=r"house blend \d",
                        help="title pattern to retire (a regex)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    log = EventLog(args.db)
    try:
        rows = stale(log.read_all(), re.compile(args.match, re.I))
        for row in rows:
            print(f"  {row['actor'][2:].replace('_', ' '):<22} "
                  f"{row['was']:<38} -> {row['now']}")
            if not args.dry_run:
                relist(log, row)
    finally:
        log.close()

    if not rows:
        print(f"  Nothing on the book matches {args.match!r}.")
        return 0
    verb = "would relist" if args.dry_run else "relisted"
    print(f"\n  {verb} {len(rows)} listings. The old ones are expired, not "
          f"erased —\n  the log still shows what they were called.")
    if not args.dry_run:
        print("  Rebuild the pages to see the new names.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
