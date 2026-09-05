"""Snapshot the log before a rehearsal, and put it back afterwards.

    .venv/bin/python -m scripts.market.rehearse --save     # once, before
    .venv/bin/python -m scripts.market.rehearse --reset    # after each run

WHY NOT JUST DELETE THE TRADES. Because asking is a real purchase, rehearsing
spends the merchant, and the obvious fix — remove those events and start again
— is the one thing this system must never do. The log is append-only, enforced
by triggers, and that is not a technicality: it is the reason a refusal
recorded at event 258 means anything. A log you can tidy up proves nothing
about what happened.

SO THIS DOES NOT EDIT A LOG. It copies the whole database file to one side
before you rehearse, and copies it back afterwards. Restoring a backup and
rewriting history are different acts: within any run, every event that was
written is still there, in order, and nothing was quietly removed. What you
are doing is starting the run again from a known point, which is what a
rehearsal is.

WHAT TO RECORD WITH. The restored file is the same log the pages are built
from, so the demo merchant is empty again and every event number in the
finished video is one that a viewer can look up.
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import sys

SUFFIX = ".rehearsal-baseline"


def baseline_for(db: str) -> pathlib.Path:
    path = pathlib.Path(db)
    return path.with_name(path.name + SUFFIX)


def counts(db: str) -> tuple:
    from exchange.eventlog import EventLog

    log = EventLog(db)
    try:
        events = log.read_all()
    finally:
        log.close()
    return len(events), (events[-1].seq if events else 0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Snapshot the log for rehearsing, and restore it.")
    parser.add_argument("--db", default="runs/market.db")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--save", action="store_true",
                       help="take the snapshot to rehearse against")
    group.add_argument("--reset", action="store_true",
                       help="put the snapshot back and rebuild the pages")
    group.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)

    live = pathlib.Path(args.db)
    baseline = baseline_for(args.db)

    if args.status:
        if not baseline.exists():
            print("  No snapshot. Take one with --save before rehearsing.")
            return 0
        now, seq = counts(args.db)
        was, was_seq = counts(str(baseline))
        print(f"  snapshot   {was:>5} events (to seq {was_seq})")
        print(f"  now        {now:>5} events (to seq {seq})")
        print(f"  rehearsal  {now - was:>5} events would be dropped by --reset")
        return 0

    if args.save:
        if not live.exists():
            print(f"  {args.db} is not there.")
            return 1
        shutil.copy2(live, baseline)
        total, seq = counts(args.db)
        print(f"  snapshot taken: {total} events, to seq {seq}.")
        print(f"  {baseline}")
        print("\n  Rehearse as much as you like. Then:")
        print("    .venv/bin/python -m scripts.market.rehearse --reset")
        return 0

    if not baseline.exists():
        print(f"  No snapshot at {baseline}.")
        print("  Take one with --save while the demo merchant is still empty.")
        return 1

    now, _ = counts(args.db)
    was, seq = counts(str(baseline))
    shutil.copy2(baseline, live)
    print(f"  restored: {was} events, to seq {seq}. "
          f"{max(0, now - was)} rehearsal events dropped.")

    from scripts.replay import generate
    generate.main([args.db, "docs"])
    print("\n  The demo merchant is empty again. Restart the server so it "
          "reads\n  the restored log:")
    print("    .venv/bin/python -m scripts.serve --merchant m_morningside "
          "--port 8795")
    return 0


if __name__ == "__main__":
    sys.exit(main())
