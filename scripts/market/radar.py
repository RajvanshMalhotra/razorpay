"""Rank the marketing campaigns companies are actually running.

    .venv/bin/python -m scripts.market.radar runs/market.db

Reads Reddit (and X, if X_BEARER_TOKEN is set); writes CAMPAIGN_RANKED rows
scoped `brand_radar`, plus a refusal for every campaign below the thread
floor. Never touches the procurement board, which is computed from settled
trades and must stay recomputable from the log alone.

Ordered deliberately: collect, group, rank. The model groups phrasings into
campaign names and never decides an order — the order is arithmetic over the
posts that were actually collected.
"""
from __future__ import annotations

import argparse
import sys

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.brands import SEED_BRANDS, discover, label, publish, rank
from exchange.ids import new_id


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Publish the brand radar.")
    parser.add_argument("db", nargs="?", default="runs/market.db")
    parser.add_argument("--brands", default="",
                        help="comma separated; defaults to the seed list")
    parser.add_argument("--floor", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the ranking without writing to the log")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    from exchange.house.brands import XAPI
    from exchange.house.socialcrawl import SocialCrawl
    from exchange.llm.openai_compat import providers_from_env

    load_dotenv()
    _, fast = providers_from_env()

    brands = (tuple(b.strip() for b in args.brands.split(",") if b.strip())
              or SEED_BRANDS)
    x = XAPI.from_env()
    crawl = SocialCrawl.from_env()
    # The ladder, printed, so a run says out loud how much of the world it
    # could actually see. A thin board from one source is a finding; a thin
    # board that looks like it read everything is a lie.
    print(f"  {len(brands)} brands | reddit rss: yes | "
          f"socialcrawl: {'yes' if crawl else 'no (set SOCIALCRAWL_API_KEY)'} | "
          f"x direct: {'yes' if x else 'no (set X_BEARER_TOKEN)'}")

    mentions = discover(brands=brands, x=x, crawl=crawl)
    if crawl is not None and crawl.credits_remaining is not None:
        print(f"  socialcrawl credits remaining: {crawl.credits_remaining}")
    print(f"  {len(mentions)} posts collected")
    if not mentions:
        print("  nothing collected; not publishing an empty radar.")
        return 1

    labels, fallbacks = label(mentions, fast)
    if fallbacks and fallbacks >= len(mentions) // 2:
        # A radar built mostly from unlabelled posts is one campaign per post,
        # which reads as a finding rather than as the model having failed.
        print(f"  REFUSING TO PUBLISH: {fallbacks} of {len(mentions)} posts "
              f"went unnamed, so the grouping is not the model's.")
        return 1
    print(f"  {len(set(labels.values()))} campaigns named "
          f"({fallbacks} posts not about a campaign)")

    kwargs = {"floor": args.floor} if args.floor is not None else {}
    ranked, refused = rank(mentions, labels, **kwargs)
    print(f"  {len(ranked)} ranked, {len(refused)} below the floor\n")

    print("  Razorpay internal — campaigns the market is talking about")
    for position, row in enumerate(ranked, start=1):
        print(f"  {position:>2}. {row.name[:44]:<44} heat {row.heat:>3}  "
              f"{row.threads} threads across {row.spread}  "
              f"{'/'.join(row.sources)}")

    if args.dry_run:
        print("\n  dry run; nothing written.")
        return 0

    log = EventLog(args.db)
    existing = [e for e in log.read_all()
                if e.type == ev.CAMPAIGN_RANKED
                and e.payload.get("scope") == "brand_radar"]
    if existing:
        print(f"\n  a radar is already published ({len(existing)} rows).")
        return 0
    publish(log, ranked, refused, new_id("radar"))
    print(f"\n  written to {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
