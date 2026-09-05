"""Publish what Razorpay computed to the businesses that pay for it.

    .venv/bin/python -m scripts.market.publish --dry-run
    .venv/bin/python -m scripts.market.publish

WHY THIS IS A STEP AND NOT A PAGE SETTING. The campaign board, the brand radar
and the price benchmarks are all computed by the house agent and marked
`audience: razorpay_internal` — because that is who they were for. Showing
them to a paying merchant without saying so would put a figure on a merchant's
screen that the log says was never published to merchants, and the whole claim
of this system is that the screen and the log agree.

So publishing is an event. `INSIGHT_PUBLISHED` records what went out, to which
plan, and which internal row it came from. A merchant on that plan sees it; a
merchant not on it does not; and anybody can check afterwards which is which.

THE PRIVACY FLOOR IS NOT RE-APPLIED HERE, AND MUST NOT BE. Rows that failed it
were never ranked in the first place — `PRIVACY_REFUSED` is beside them in the
log with the merchant count that failed. Re-checking here would imply the
floor is a publishing decision. It is a computation decision, made once,
upstream, where the data still exists to make it.
"""
from __future__ import annotations

import argparse
import sys

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.insights import HOUSE_ACTOR_ID
from exchange.ids import new_id

PLAN = "market"


def unpublished(events) -> list:
    """Internal rows that the paying plan has not been given yet."""
    done = {(e.payload or {}).get("source_seq") for e in events
            if e.type == ev.INSIGHT_PUBLISHED}

    out = []
    for event in events:
        payload = event.payload or {}
        if payload.get("audience") != "razorpay_internal":
            continue
        if event.type not in (ev.CAMPAIGN_RANKED, ev.BENCHMARK_PUBLISHED):
            continue
        if event.seq in done:
            continue
        out.append({
            "seq": event.seq,
            "scope": payload.get("scope") or (
                "campaign_board" if event.type == ev.CAMPAIGN_RANKED
                else "price_benchmark"),
            "campaign": payload.get("campaign") or payload.get("category", ""),
            "rank": payload.get("rank"),
            "payload": payload,
        })
    return out


def publish(log, rows, plan: str) -> str:
    corr = new_id("publish")
    for row in rows:
        body = dict(row["payload"])
        body.update({
            # The scope was worked out when the row was found and then thrown
            # away — a board row's own payload does not carry one, so it
            # arrived on the other side as "other".
            "scope": row["scope"],
            "audience": f"plan:{plan}",
            "plan_tier": plan,
            # WHICH INTERNAL ROW THIS IS. Not a copy floating free: a reader
            # can walk back to the event that computed the figure, and the
            # privacy refusal that sits beside it.
            "source_seq": row["seq"],
        })
        log.append(HOUSE_ACTOR_ID, ev.INSIGHT_PUBLISHED, body,
                   correlation_id=corr)
    return corr


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish house intelligence to a paid plan.")
    parser.add_argument("--db", default="runs/market.db")
    parser.add_argument("--plan", default=PLAN)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    log = EventLog(args.db)
    try:
        rows = unpublished(log.read_all())
        by_scope: dict = {}
        for row in rows:
            by_scope.setdefault(row["scope"], []).append(row)
        for scope, group in sorted(by_scope.items()):
            print(f"  {scope}  ({len(group)})")
            for row in group[:6]:
                print(f"      {row['rank'] or '-':>2}  {row['campaign'][:52]}")
        if rows and not args.dry_run:
            publish(log, rows, args.plan)
    finally:
        log.close()

    if not rows:
        print(f"  Everything computed has already been published to "
              f"'{args.plan}'.")
        return 0
    verb = "would publish" if args.dry_run else "published"
    print(f"\n  {verb} {len(rows)} rows to businesses on the "
          f"'{args.plan}' plan.")
    if not args.dry_run:
        print("  Rebuild the pages. Merchants on that plan will see them; "
              "merchants\n  on Standard will not, and their pages are built "
              "without the figures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
