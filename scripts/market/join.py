"""Register a new merchant, with no trading history.

    .venv/bin/python -m scripts.market.join m_sunrise

FOR THE LIVE DEMO. Every merchant in the finished run has already traded, so
every page shows a business mid-story. A demo that runs the agents live needs
one page that is genuinely empty at the start and fills up while people watch
— which is a real signing-up merchant, not a cleared one.

Appends one ACTOR_REGISTERED and nothing else. The log stays append-only and
the new merchant is as real as the other thirty-two; it simply has not
traded yet.
"""
from __future__ import annotations

import argparse
import sys

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.ids import new_id


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Register a merchant.")
    parser.add_argument("actor_id", help="e.g. m_sunrise")
    parser.add_argument("--db", default="runs/market.db")
    parser.add_argument("--plan", default="standard")
    args = parser.parse_args(argv)

    if not args.actor_id.startswith("m_"):
        print("  merchant ids start with m_ so the log can tell them from "
              "the house, the gate and the accountant.")
        return 1

    log = EventLog(args.db)
    if any(e.type == ev.ACTOR_REGISTERED
           and e.payload.get("actor_id") == args.actor_id
           for e in log.read_all()):
        print(f"  {args.actor_id} is already on the exchange.")
        return 0

    log.append(args.actor_id, ev.ACTOR_REGISTERED, {
        "actor_id": args.actor_id,
        "kind": "MERCHANT",
        "merchant_id": None,
        "plan_tier": args.plan,
        "status": "ACTIVE",
    }, correlation_id=new_id("join"))
    print(f"  {args.actor_id} joined on the {args.plan} plan, with no "
          f"trading history.")
    print("  Regenerate the pages and its dashboard will be empty.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
