"""Run the market.

Every phase is a separate flag and every phase is resumable, because a
two-hour run against a paid model and a live gateway WILL be interrupted and
the operator's next move is to run the same command again. That has to be free.

    # exercise the whole flow for nothing, against fakes
    .venv/bin/python -m scripts.market.main --dry-run --seed --rounds 2 --house

    # the real thing, one phase at a time
    .venv/bin/python -m scripts.market.main --seed
    .venv/bin/python -m scripts.market.main --rounds 4 --budget-turns 200
    .venv/bin/python -m scripts.market.main --pending          # what to pay
    .venv/bin/python -m scripts.market.main --house
    .venv/bin/python -m scripts.market.main --inject-failure

PAYMENT IS NOT A PHASE HERE, and that is deliberate. A settlement completes
only when someone pays its link — probed, not assumed: the server-side payment
endpoint returns 403 on this account. `--pending` prints what is outstanding;
paying it needs a live browser session, uses NETBANKING (cards are rejected:
"International cards are not supported"), and is driven by the operator. A
module that both decides what to pay and pays it is one bug away from paying
twice, and in test mode a second payment is as real as the first.
"""
from __future__ import annotations

import argparse
import sys

import razorpay
from dotenv import load_dotenv

from exchange.agents.broker import Broker
from exchange.config import Config
from exchange.eventlog import EventLog
from exchange.house.agent import HouseAgent
from exchange.llm.openai_compat import providers_from_env
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex, default_embedder
from exchange.service import Exchange
from scripts.market.clerk import pending_payments
from scripts.market.house_cycle import run_house_cycle
from scripts.market.inject_failure import handle_drift
from scripts.market.roster import MERCHANTS
from scripts.market.run import Budget, run_round
from scripts.market.seed import seed

DEFAULT_DB = "runs/market.db"


def _providers(dry_run: bool):
    if not dry_run:
        return providers_from_env()

    from exchange.llm.base import LLMResponse

    import re

    def _first_int(pattern, text, default):
        found = re.search(pattern, text, re.IGNORECASE)
        return int(found.group(1)) if found else default

    class Scripted:
        """Enough to exercise every path, and it reads its own prompt.

        A stub that answers a fixed price ignores that every merchant posted
        a different ceiling: the first version replied "PRICE: 24500" to a
        packaging merchant whose limit was 2300, so 25 of 55 turns were
        refused for a reason no real negotiation would produce. The dry run
        then looked like a market that mostly fails, which is a misleading
        thing to check the wiring against.

        This one takes the lowest figure quoted in the prompt as the
        counterparty's ceiling and comes in just under it — crude, but it
        exercises the settled path for every merchant rather than only the
        richest ones.
        """

        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            prompt = " ".join(m.content for m in messages)

            lowered = prompt.lower()

            if "write the headline" in lowered:
                merchants = _first_int(r"across (\d+) merchants", prompt, 0)
                return LLMResponse(
                    text=("Cold brew concentrate demand is rising across "
                          f"{merchants} Bangalore merchants"),
                    input_tokens=8, output_tokens=8, model="dry-run")

            if "bid at most" in lowered:
                cap = _first_int(r"bid at most (\d+)", prompt, 500)
                # Bids must DIFFER, or every broker ties at one number and the
                # winner is whoever the dict happened to yield first — which
                # in the first dry run was a supplier holding no points, so
                # the purchase failed and no royalty ever moved. Second price
                # also needs a genuine second price to be worth anything.
                spread = 40 + (self.calls * 7) % 55        # 40-95% of the cap
                return LLMResponse(
                    text=f"BID: {max(1, cap * spread // 100)} worth it for the "
                         "category read",
                    input_tokens=8, output_tokens=8, model="dry-run")

            # The prompt states this side's own bound in words. Read THAT
            # rather than picking a number out of the transcript — guessing
            # produced a stub that offered below the seller's floor and made
            # all 55 negotiations walk.
            ceiling = _first_int(r"not pay above (\d+)", prompt, None)
            floor = _first_int(r"not sell below (\d+)", prompt, None)

            # AGREEMENT IS BOTH SIDES QUOTING THE SAME NUMBER, so a stub that
            # only ever repeats its own figure never agrees — it stalls, which
            # is what an earlier version did on all 55 turns.
            offered = re.findall(r"PRICE:\s*(\d+)", prompt, re.IGNORECASE)
            theirs = int(offered[-1]) if offered else None

            if floor is not None:
                # Selling: take anything at or above the floor.
                if theirs is not None and theirs >= floor:
                    return LLMResponse(
                        text=f"PRICE: {theirs} agreed, we can work with that",
                        input_tokens=8, output_tokens=8, model="dry-run")
                offer = floor + max(1, floor // 50)
            else:
                # Buying, AND ACTUALLY HAGGLING. An earlier version offered
                # just under its own ceiling — which sits above the ask — so
                # every merchant paid more than the asking price, captured no
                # margin, and earned nothing. POINTS_MINTED came out at zero
                # and the auction winner then could not afford its own bid.
                #
                # That was the points rule working exactly as written: it pays
                # for margin captured, never for volume. A stub that does not
                # negotiate should not earn, so the stub has to negotiate.
                cap = ceiling if ceiling is not None else 10**9
                if theirs is not None:
                    counter = theirs - max(1, theirs // 10)   # ask for 10% off
                    if counter <= 0:
                        counter = theirs
                    offer = min(cap, counter)
                    # Accept rather than haggle forever once it is close.
                    if theirs <= cap and theirs - offer <= max(1, theirs // 50):
                        return LLMResponse(
                            text=f"PRICE: {theirs} agreed, we can work with that",
                            input_tokens=8, output_tokens=8, model="dry-run")
                else:
                    offer = cap - max(1, cap // 10)
            return LLMResponse(text=f"PRICE: {max(1, offer)} that works for us",
                               input_tokens=8, output_tokens=8, model="dry-run")

    scripted = Scripted()
    return scripted, scripted


class _DryRunRazorpay:
    """Every link is paid the instant it is made.

    A dry run whose settlements all stay PENDING exercises about a third of
    the system: no fills, no mints, confidence pinned at zero, and the house
    agent mining an empty set. That is exactly what the first dry run did,
    and it printed `refused: derived from 0 distinct merchants` — a faithful
    reproduction of the real problem, and useless for checking the wiring
    behind it.

    So this one pays. It still models the shape live test mode actually has:
    the receipt order never holds a payment, and the capture is visible only
    through the link.
    """

    def __init__(self):
        self.orders = 0
        self.links = 0
        outer = self

        class _Orders:
            @staticmethod
            def create(data):
                outer.orders += 1
                return {"id": f"order_dry_{outer.orders}",
                        "amount": data["amount"], "status": "created"}

            @staticmethod
            def payments(order_id):
                return {"count": 0, "items": []}

        class _Links:
            @staticmethod
            def create(data):
                outer.links += 1
                return {"id": f"plink_dry_{outer.links}",
                        "short_url": f"https://rzp.io/rzp/DRY{outer.links}"}

            @staticmethod
            def fetch(link_id):
                return {"id": link_id, "status": "paid",
                        "order_id": f"order_by_{link_id}",
                        "payments": [{"payment_id": f"pay_{link_id}",
                                      "status": "captured"}]}

        self.order = _Orders()
        self.payment_link = _Links()


def _client(dry_run: bool):
    if dry_run:
        return _DryRunRazorpay()
    cfg = Config.from_env()
    return razorpay.Client(auth=(cfg.razorpay_key_id, cfg.razorpay_key_secret))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the agent market.")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--seed", action="store_true",
                        help="register merchants, list assets, post asks")
    parser.add_argument("--rounds", type=int, default=0,
                        help="trade this many rounds")
    parser.add_argument("--house", action="store_true",
                        help="mine, auction and pay royalties")
    parser.add_argument("--pending", action="store_true",
                        help="print settlements still awaiting payment")
    parser.add_argument("--inject-failure", action="store_true",
                        help="reconcile, and contain any drift found")
    parser.add_argument("--budget-turns", type=int, default=200)
    parser.add_argument("--budget-seconds", type=float, default=3600.0)
    parser.add_argument("--dry-run", action="store_true",
                        help="fakes throughout; spends nothing")
    args = parser.parse_args(argv)

    if not any((args.seed, args.rounds, args.house, args.pending,
                args.inject_failure)):
        parser.print_help()
        return 2

    load_dotenv()
    strong, fast = _providers(args.dry_run)
    client = _client(args.dry_run)

    log = EventLog(args.db)
    try:
        exchange = Exchange(
            log,
            HybridIndex(embed_fn=default_embedder()),
            RazorpayRail(log, client),
            CreditRail(log),
        )
        print(f"database: {args.db}"
              f"{'   [DRY RUN — nothing is spent]' if args.dry_run else ''}\n")

        if args.seed:
            print(f"seed: {seed(exchange, MERCHANTS)}")

        if args.rounds:
            brokers = {
                m.actor_id: Broker(m.actor_id, exchange, strong, fast_provider=fast)
                for m in MERCHANTS
            }
            budget = Budget(max_turns=args.budget_turns,
                            max_seconds=args.budget_seconds)
            for round_no in range(1, args.rounds + 1):
                print(run_round(exchange, brokers, MERCHANTS, round_no, budget))
                if budget.exhausted():
                    print(f"  budget spent after round {round_no}; "
                          f"re-run to continue")
                    break

        if args.pending:
            report = pending_payments(log)
            print(f"payments: {report}")
            for payable in report.payable[:20]:
                print(f"  {payable.settlement_id}  "
                      f"{payable.amount / 100:>10,.2f}  {payable.payment_link_url}")
            if len(report.payable) > 20:
                print(f"  ... and {len(report.payable) - 20} more")
            for unpayable in report.unpayable:
                print(f"  UNPAYABLE {unpayable.settlement_id}: {unpayable.reason}")

        if args.house:
            brokers = {
                m.actor_id: Broker(m.actor_id, exchange, strong, fast_provider=fast)
                for m in MERCHANTS
            }
            house = HouseAgent(log, strong)
            print(f"house: {run_house_cycle(exchange, house, brokers, 'house_cycle')}")

        if args.inject_failure:
            print(f"failure: {handle_drift(exchange, client)}")

        return 0
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
