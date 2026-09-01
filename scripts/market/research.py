"""Run the house research desk over a finished market and publish the board.

    .venv/bin/python -m scripts.market.research runs/market.db

Reads only; writes only CAMPAIGN_RANKED and PRIVACY_REFUSED rows, all on one
correlation id, so the board can be pulled back out as a unit — including
the campaigns that were refused a place on it.

Ordered deliberately: group, rank, then research. The ranking is settled
before anything reads a headline or a thread, which is the property the
board's whole credibility rests on.

Two outside sources, attached separately. `--no-reddit` skips the second
when you want a board fast, or when Reddit is throttling and you would
rather ship the press alone than a page of refusal notes.
"""
from __future__ import annotations

import argparse
import sys

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.campaigns import (
    is_board_row,
    label,
    observe,
    publish,
    rank,
    research,
)
from exchange.ids import new_id


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Publish the campaign board.")
    parser.add_argument("db", nargs="?", default="runs/market.db")
    parser.add_argument("--top", type=int, default=5,
                        help="how many rows get press research attached")
    parser.add_argument("--floor", type=int, default=None,
                        help="override the board's minimum merchant count")
    parser.add_argument("--no-reddit", action="store_true",
                        help="attach the press only, and skip the discussion")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    from exchange.llm.openai_compat import providers_from_env

    load_dotenv()
    _, fast = providers_from_env()

    social = None
    if not args.no_reddit:
        from exchange.house import social as social_mod

        # The official API when there are credentials for it, the public RSS
        # when there are not. The RSS path needs nothing and throttles hard,
        # so a board built on it will carry some refusal notes — which is the
        # honest outcome, and why the field saying so exists.
        api = social_mod.RedditAPI.from_env()
        print(f"  reddit: {'official API' if api else 'public RSS (throttled)'}")
        social = lambda topic: social_mod.research_topic(  # noqa: E731
            topic, provider=fast, api=api)

    log = EventLog(args.db)
    try:
        events = log.read_all()
        existing = [e for e in events
                    if e.type == ev.CAMPAIGN_RANKED and is_board_row(e)]
        if existing:
            print(f"  a board is already published "
                  f"({len(existing)} rows, {existing[0].correlation_id}).")
            print("  delete those rows or use a fresh log to republish.")
            return 0

        turns = observe(events)
        print(f"  {len(turns)} turns read from the log")

        labels, fallbacks = label([t.need for t in turns], fast)
        print(f"  {len(set(labels.values()))} campaigns named")
        if fallbacks:
            # Loud, and it stops here. A board built mostly from fallbacks is
            # one campaign per phrase — which reads as a finding rather than
            # as the model having returned nothing.
            print(f"  REFUSING TO PUBLISH: the model left {fallbacks} of "
                  f"{len(labels)} phrases unlabelled, so the grouping is not "
                  f"the model's. Re-run; if it persists, raise the budget in "
                  f"campaigns.label.")
            return 1

        kwargs = {"floor": args.floor} if args.floor is not None else {}
        ranked, refused = rank(turns, labels, **kwargs)
        print(f"  {len(ranked)} ranked, {len(refused)} below the floor")

        for campaign in ranked[:args.top]:
            research(campaign, fast, social=social)
            note = f"{len(campaign.sources)} sources"
            if social is not None:
                note += (", reddit refused" if campaign.discussion_blocked
                         else f", {len(campaign.threads)} threads")
            print(f"    {campaign.name}: {note}")

        correlation_id = new_id("research")
        publish(log, ranked, refused, correlation_id)

        print(f"\n  Razorpay internal — trending client campaigns")
        for position, campaign in enumerate(ranked, start=1):
            movement = (f"{campaign.movement:.1f}x" if campaign.movement
                        else "new")
            print(f"  {position:>2}. {campaign.name:<28} {movement:>6}  "
                  f"{campaign.value_paise / 100:>12,.0f} rupees  "
                  f"{campaign.k:>2} merchants")
            if campaign.driver:
                print(f"      {campaign.driver[:110]}")
        print(f"\n  board correlation id: {correlation_id}")
        return 0
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
