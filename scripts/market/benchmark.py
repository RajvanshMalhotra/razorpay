"""What each category actually clears at, published from the log.

    .venv/bin/python -m scripts.market.benchmark runs/market.db

No model, no network, no credits. A seller sees its own asks and a buyer sees
its own bills; only the party that settles both sides of every trade can say
what the middle is.
"""
from __future__ import annotations

import argparse
import sys

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.benchmarks import headline, observe, publish, rank
from exchange.ids import new_id


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Publish price benchmarks.")
    parser.add_argument("db", nargs="?", default="runs/market.db")
    parser.add_argument("--floor", type=int, default=None,
                        help="minimum distinct merchants behind a benchmark")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    log = EventLog(args.db)
    events = log.read_all()
    if any(e.type == ev.BENCHMARK_PUBLISHED for e in events):
        print("  benchmarks are already published on this log.")
        return 0

    fills = observe(events)
    if not fills:
        print("  no priced matches. Publish a campaign board first so trades "
              "can be grouped into categories.")
        return 1

    rows, refused = rank(fills, floor=args.floor)
    print(f"  {len(fills)} priced matches, {len(rows)} categories, "
          f"{len(refused)} below the floor\n")
    print(f"  {'category':<24}{'trades':>7}{'clears':>9}{'ask':>8}"
          f"{'below ask':>11}{'saving':>9}")
    for row in rows:
        print(f"  {row.category:<24}{row.trades:>7}"
              f"{row.clears_paise / 100:>9,.0f}{row.ask_paise / 100:>8,.0f}"
              f"{row.below_ask_share * 100:>10.0f}%"
              f"{row.median_saving * 100:>8.1f}%")
    print(f"\n  free headline: {headline(rows)}")

    if args.dry_run:
        print("\n  dry run; nothing written.")
        return 0
    publish(log, rows, refused, new_id("bench"))
    print(f"\n  written to {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
