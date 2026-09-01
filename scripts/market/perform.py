"""Did the campaigns convert? Read from the log, costing nothing.

    .venv/bin/python -m scripts.market.perform runs/market.db

No model, no network, no credits. The board above it needed the press and
Reddit; this needs only the settlements Razorpay already wrote down, which is
the one thing no competitor to Razorpay can read.
"""
from __future__ import annotations

import argparse
import sys

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.performance import observe, publish, rank
from exchange.ids import new_id


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Publish campaign performance.")
    parser.add_argument("db", nargs="?", default="runs/market.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    log = EventLog(args.db)
    events = log.read_all()
    if any(e.type == ev.CAMPAIGN_PERFORMANCE for e in events):
        print("  performance is already published on this log.")
        return 0

    attempts, unmatched = observe(events)
    if not attempts:
        print("  no campaign board published, so nothing to measure against.")
        return 1
    rows = rank(attempts)

    print(f"  {len(attempts)} turns followed through to settlement, "
          f"{unmatched} on no published row\n")
    print(f"  {'campaign':<24}{'asked':>6}{'paid':>6}{'settled':>9}"
          f"{'revenue':>12}{'AOV':>9}{'to pay':>8}{'stopped':>9}")
    for row in rows:
        pay = f"{row.median_seconds // 60}m" if row.median_seconds else "-"
        print(f"  {row.campaign:<24}{row.links:>6}{row.paid:>6}"
              f"{row.conversion * 100:>6.0f}%"
              f"{row.revenue_paise / 100:>12,.0f}"
              f"{row.aov_paise / 100:>9,.0f}{pay:>8}{row.stopped:>9}")

    if args.dry_run:
        print("\n  dry run; nothing written.")
        return 0
    publish(log, rows, unmatched, new_id("perf"))
    print(f"\n  written to {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
