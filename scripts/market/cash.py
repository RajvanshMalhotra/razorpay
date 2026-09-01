"""What each campaign earned, read from a real Razorpay account.

    .venv/bin/python -m scripts.market.cash

Reads the account named by RAZORPAY_KEY_ID and asks it what its payment links
did. No event log, no simulation — point it at any Razorpay key and it
answers. That is the difference between this and `scripts.market.perform`,
which computes the same figures from this project's own log.

Tag a link when you create it and it appears under that campaign here:

    notes = {"campaign": "diwali_instagram"}

Untagged links are still counted, under one honest heading at the bottom.
"""
from __future__ import annotations

import argparse
import sys

from exchange.house.attribution import UNTAGGED, rank, read_links


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Campaign to cash, live.")
    parser.add_argument("--limit", type=int, default=100,
                        help="how many payment links to read")
    args = parser.parse_args(argv)

    import razorpay
    from dotenv import load_dotenv

    from exchange.config import Config

    load_dotenv()
    config = Config.from_env()
    client = razorpay.Client(auth=(config.razorpay_key_id,
                                   config.razorpay_key_secret))

    print(f"  account {config.razorpay_key_id[:16]}...")
    try:
        links = read_links(client, limit=args.limit)
    except Exception as exc:  # noqa: BLE001
        # Loud, and it stops here. An account that could not be read must
        # never be reported as an account with nothing in it.
        print(f"  COULD NOT READ THE ACCOUNT: {type(exc).__name__}: {exc}")
        return 1

    if not links:
        print("  the account has no payment links.")
        return 0

    rows = rank(links)
    tagged = [r for r in rows if r.campaign != UNTAGGED]
    print(f"  {len(links)} payment links, {len(tagged)} campaign"
          f"{'' if len(tagged) == 1 else 's'} tagged\n")
    print(f"  {'campaign':<26}{'issued':>7}{'paid':>6}{'settled':>9}"
          f"{'revenue':>12}{'AOV':>10}")
    for row in rows:
        print(f"  {row.campaign[:26]:<26}{row.issued:>7}{row.paid:>6}"
              f"{row.settled_share * 100:>8.0f}%"
              f"{row.revenue_paise / 100:>12,.0f}{row.aov_paise / 100:>10,.0f}")

    if not tagged:
        print("\n  Nothing is tagged yet, so this is one undifferentiated "
              "total.\n  Pass campaign= when creating a link and the rows "
              "split by campaign.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
