"""One command for a brand new merchant to demo with.

    .venv/bin/python -m scripts.market.fresh
    .venv/bin/python -m scripts.market.fresh --name m_meridian

WHY. Asking is a real purchase, so the merchant you rehearse on is spent: it
ends the rehearsal with a supplier, a payment and a book that is no longer
empty. That is the product working, and it means "starts from nothing" is a
one-shot demo. Getting a clean one back was four commands, and the first of
them silently did nothing if you reused a name — printing "already on the
exchange" and leaving you with the spent merchant you were trying to escape.

So this picks an unused name, registers it, gives it a shelf, rebuilds the
pages, and prints the one line you need next. Nothing here is destructive: the
spent merchants keep their trades, because those trades happened.
"""
from __future__ import annotations

import argparse
import sys

# Sunrises, because that is what the demo merchant always is: a business on
# its first morning. Walked in order, so a name is never reused.
NAMES = ("dawn", "horizon", "meridian", "daylight", "morningside", "aurora",
         "eastgate", "firstlight", "daybreak", "sunup", "cockcrow", "kindle")


def unused(events) -> str | None:
    taken = {e.actor_id for e in events}
    for name in NAMES:
        if f"m_{name}" not in taken:
            return f"m_{name}"
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="A clean merchant to demo with.")
    parser.add_argument("--db", default="runs/market.db")
    parser.add_argument("--name", default=None,
                        help="a specific id, e.g. m_meridian")
    parser.add_argument("--port", type=int, default=8795)
    args = parser.parse_args(argv)

    from exchange.eventlog import EventLog
    from scripts.market import join, stock
    from scripts.replay import generate

    log = EventLog(args.db)
    try:
        events = log.read_all()
    finally:
        log.close()

    actor = args.name or unused(events)
    if actor is None:
        print("  Every name is used. Pass one of your own with --name.")
        return 1
    if actor in {e.actor_id for e in events}:
        print(f"  {actor[2:]} is already on the exchange, and may have traded.")
        print("  Leave --name off and one will be picked that has not.")
        return 1

    if join.main([actor, "--db", args.db]) != 0:
        return 1
    stock.main(["--db", args.db, "--merchant", actor])
    generate.main([args.db, "docs"])

    name = actor[2:].replace("_", " ")
    page = f"m-{actor[2:].replace('_', '-')}.html"
    print(f"\n  {name} has never traded. Five things listed, nothing bought.")
    print(f"\n  .venv/bin/python -m scripts.serve --merchant {actor} "
          f"--port {args.port}")
    print(f"  then open  http://localhost:{args.port}/{page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
