"""Put a merchant on the plan that includes the market read, or take it off.

    .venv/bin/python -m scripts.market.subscribe m_bl_thirdwave --plan market
    .venv/bin/python -m scripts.market.subscribe m_bl_thirdwave --plan standard

WHY THIS IS AN EVENT AND NOT A SETTING. The whole product claim is that the
market read is bought, and something that is bought has to be a fact anybody
can check afterwards: who subscribed, when, and what they could see as a
result. A boolean in a config file proves none of that, and a page that
merely hides a figure is not a paid product — it is a paid product's
screenshot.

WHAT THE PLAN ACTUALLY BUYS. `standard` sees that a gap exists. `market` sees
what the gap is: the price each category clears at, the seller's ask beside
it, and how often sellers move. The first is free because it is useless on
its own; the second is the thing worth money.

THE LOCK IS IN THE BUILDER, NOT THE BROWSER. Pages for a standard merchant
are rendered without the numbers in them at all — not hidden with CSS, not
sitting in a JSON payload behind a class. A lock you can open with developer
tools is a decoration.
"""
from __future__ import annotations

import argparse
import sys

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.ids import new_id

PLANS = ("standard", "market")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Change a merchant's plan.")
    parser.add_argument("actor_id", help="e.g. m_bl_thirdwave")
    parser.add_argument("--plan", default="market", choices=PLANS)
    parser.add_argument("--db", default="runs/market.db")
    args = parser.parse_args(argv)

    log = EventLog(args.db)
    try:
        events = log.read_all()
        registered = [e for e in events
                      if e.type == ev.ACTOR_REGISTERED
                      and e.payload.get("actor_id") == args.actor_id]
        if not registered:
            print(f"  {args.actor_id} is not on the exchange. Register it "
                  f"first with scripts.market.join.")
            return 1

        log.append(args.actor_id, ev.PLAN_CHANGED, {
            "actor_id": args.actor_id,
            "plan_tier": args.plan,
        }, correlation_id=new_id("plan"))
    finally:
        log.close()

    name = args.actor_id[2:].replace("_", " ")
    if args.plan == "market":
        print(f"  {name} is on Standard + Market.")
        print("  Its page now shows what each category clears at, the ask "
              "beside it, and how often sellers move.")
    else:
        print(f"  {name} is on Standard.")
        print("  Its page shows that a gap exists and not what it is.")
    print("  Rebuild the pages to see it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
