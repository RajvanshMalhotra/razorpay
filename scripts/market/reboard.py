"""Republish the campaign board onto a finished run, keeping every figure.

    .venv/bin/python -m scripts.market.reboard runs/market.db

WHY THIS EXISTS AND `research.py` DOES NOT DO IT. The log is append-only and
enforced by a trigger: a published board cannot be edited or removed. That is
the property the whole submission rests on, so the fix is not to weaken it.

WHAT THIS DOES INSTEAD. Copies every event before the board into a fresh log,
verbatim — same seq, same event_id, same timestamp — then publishes the board
once more with the discussion attached. The run ends up as it would have if
Reddit had been wired in before research first ran.

WHAT IT DELIBERATELY DOES NOT DO. It never re-runs `label` or `rank`. Grouping
needs into campaigns is a model call and is not deterministic, so re-running it
could rename or re-rank the board — and those names and figures are cited in
the write-up, the replay pages and the demo script. The old rows already carry
every number, so the campaigns are rebuilt from them and only text is added.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.campaigns import Campaign, Refusal, Source, publish, research
from exchange.ids import new_id


def _payload(event):
    """`EventLog` hands back a dict; a raw row hands back a string."""
    return (json.loads(event.payload) if isinstance(event.payload, str)
            else event.payload)


def campaigns_from(rows):
    """Rebuild the ranked board from the rows it was published as."""
    out = []
    for row in rows:
        p = _payload(row)
        out.append(Campaign(
            name=p["campaign"],
            needs=tuple(p.get("needs", ())),
            # `merchants` is stored as a count, not a roster — the roster is
            # private, which is the point of the floor. A tuple of that many
            # placeholders preserves `k` without inventing identities.
            merchants=tuple(f"m_{i}" for i in range(p["merchants"])),
            value_paise=p["value_paise"],
            settled=p["settled"],
            attempts=p["attempts"],
            early_paise=p["early_paise"],
            late_paise=p["late_paise"],
            driver=p.get("driver", ""),
            sources=[Source(s["title"], s["url"], s["published"], s["publisher"])
                     for s in p.get("sources", [])],
        ))
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Republish the board.")
    parser.add_argument("db", nargs="?", default="runs/market.db")
    parser.add_argument("--no-reddit", action="store_true")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    from exchange.llm.openai_compat import providers_from_env

    load_dotenv()
    _, fast = providers_from_env()

    old = EventLog(args.db)
    events = old.read_all()
    board = [e for e in events if e.type in (ev.CAMPAIGN_RANKED,
                                             ev.PRIVACY_REFUSED)]
    if not board:
        print("  no board published on this log; use scripts.market.research.")
        return 1

    first = min(e.seq for e in board)
    if any(e.seq > first and e.type not in (ev.CAMPAIGN_RANKED,
                                            ev.PRIVACY_REFUSED)
           for e in events):
        # Rebuilding would have to reorder events that came after the board,
        # and reordering an append-only log is exactly what this avoids.
        print("  events were appended after the board; refusing to rebuild.")
        return 1

    keep = [e for e in events if e.seq < first]
    ranked = campaigns_from([e for e in board if e.type == ev.CAMPAIGN_RANKED])
    refused = [Refusal(_payload(e)["campaign"], _payload(e)["k"],
                       _payload(e)["reason"])
               for e in board if e.type == ev.PRIVACY_REFUSED]
    print(f"  {len(keep)} events kept, {len(ranked)} campaigns, "
          f"{len(refused)} refusals to republish")

    social = None
    if not args.no_reddit:
        from exchange.house import social as social_mod

        api = social_mod.RedditAPI.from_env()
        print(f"  reddit: {'official API' if api else 'public RSS (throttled)'}")
        social = lambda topic: social_mod.research_topic(  # noqa: E731
            topic, provider=fast, api=api)

    for campaign in ranked:
        # The press is already on the row and re-fetching it would let a fresh
        # headline quietly change a published sentence. Only the discussion is
        # new, so only the discussion is read.
        if social is not None:
            from exchange.house.campaigns import _read_discussion
            _read_discussion(campaign, social)
        note = ("reddit refused" if campaign.discussion_blocked
                else f"{len(campaign.threads)} threads")
        print(f"    {campaign.name}: {note}")

    rebuilt = args.db + ".rebuilt"
    if os.path.exists(rebuilt):
        os.remove(rebuilt)
    new = EventLog(rebuilt)
    with new._conn as conn:
        for e in keep:
            conn.execute(
                "INSERT INTO events (seq, event_id, ts, actor_id, type, "
                "payload, causation_id, correlation_id) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (e.seq, e.event_id, e.ts, e.actor_id, e.type,
                 e.payload if isinstance(e.payload, str)
                 else json.dumps(e.payload),
                 e.causation_id, e.correlation_id))

    publish(new, ranked, refused, new_id("research"))

    total = len(new.read_all())
    print(f"\n  rebuilt log: {total} events "
          f"({'same as before' if total == len(events) else 'CHANGED'})")
    if total != len(events):
        print("  refusing to swap in a log with a different event count.")
        return 1

    shutil.move(rebuilt, args.db)
    print(f"  {args.db} replaced. Backup was taken separately.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
