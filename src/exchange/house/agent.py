"""Razorpay's own agent. It mints, publishes and clears; it never buys.

It reads REAL settled activity out of the event log rather than a seeded
corpus, because that is what makes the claim true: this product exists only
for whoever is processing the payments.
"""
from __future__ import annotations

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.insights import HOUSE_ACTOR_ID, check_privacy, mint_lot
from exchange.llm.base import LLMMessage, LLMProvider

HEADLINE_PROMPT = """You are Razorpay's market research agent. You see settled
transactions across many merchants and write one headline naming a pattern worth
paying for.

Write ONE sentence. Name the category and the direction. Never name a merchant,
never quote a single transaction, and never include a figure that could identify
one business."""

# THE HEADLINE IS THE FREE HALF AND THE PLAYBOOK IS THE PAID HALF, so the
# headline has to be worth reading and still worth paying past. A teaser that
# gives the answer away leaves nothing to auction; one that says nothing at
# all is not a signal, it is an advert.
BOARD_HEADLINE_PROMPT = """You are Razorpay's market research agent, writing the
free headline above a paid lot. You can see which categories are climbing across
the whole client base and what businesses in them are saying to each other.

Write ONE sentence that is genuinely useful on its own and still leaves a reason
to buy the detail. Say that something is moving and roughly how much. Do NOT name
the leading category, do not list the categories, do not name a merchant, and do
not quote a single transaction."""


class HouseAgent:
    def __init__(self, log: EventLog, provider: LLMProvider) -> None:
        self._log = log
        self._provider = provider
        self._lots: list = []

    def observe(self) -> list[dict]:
        """Settled INR activity, one row per completed settlement.

        Only COMPLETED counts. A PENDING settlement is a payment nobody has
        made yet, and treating it as evidence would let unpaid intent become
        market intelligence.
        """
        events = self._log.read_all()
        initiated = {
            e.payload["settlement_id"]: e
            for e in events
            if e.type == ev.SETTLEMENT_INITIATED
        }
        out = []
        for event in events:
            if event.type != ev.SETTLEMENT_COMPLETED:
                continue
            opened = initiated.get(event.payload["settlement_id"])
            if opened is None:
                continue
            out.append({
                "actor_id": opened.actor_id,
                "amount": opened.payload.get("amount", 0),
                "currency": opened.payload.get("currency", "INR"),
            })
        return out

    def mint_from(self, observations: list[dict], correlation_id: str,
                  board=None):
        """Turn observations into a lot, or refuse and say why.

        The refusal is logged as loudly as the success — a floor nobody can
        see is indistinguishable from no floor.

        WHAT THE WINNER ACTUALLY GETS. Without `board` the playbook was two
        integers — a trade count and a total — which is not something a
        business would pay points for, and made the auction a ritual rather
        than a market. Given the campaign board, the playbook becomes the
        board: which categories are climbing, by how much, what those
        businesses are trying to buy, and what operators in them are saying.
        That is the thing the free headline is a teaser for.

        THE FLOOR STILL BINDS, and it binds on the same contributors as
        before: the merchants whose settled trades produced the observations.
        The board is a description of their activity, so publishing it in a
        lot is publishing their activity, and it may not clear a lower bar
        than the activity itself would.
        """
        contributors = [o["actor_id"] for o in observations]
        verdict = check_privacy(contributors)
        if not verdict.allowed:
            self._log.append(
                HOUSE_ACTOR_ID,
                ev.PRIVACY_REFUSED,
                {"reason": verdict.reason, "k": verdict.k},
                correlation_id=correlation_id,
            )
            return None

        total = sum(o["amount"] for o in observations)
        if board:
            # Movements only. Naming the categories here would put the paid
            # half into the free half.
            moves = ", ".join(f"{row['movement']:.1f}x" for row in board)
            ask = (f"{len(board)} categories ranked across {verdict.k} "
                   f"merchants, moving {moves}. Write the headline.")
        else:
            ask = (f"{len(observations)} settled trades across {verdict.k} "
                   f"merchants, totalling {total} paise. Write the headline.")
        response = self._provider.complete(
            [LLMMessage("user", ask)],
            system=BOARD_HEADLINE_PROMPT if board else HEADLINE_PROMPT,
            # 120 was not a tight budget, it was an empty one: this model
            # spent all of it reasoning and returned "". The headline is what
            # appears on screen, so it gets room to think (~529 tokens) and
            # then room to answer.
            max_tokens=800,
            reasoning_effort="low",
        )
        headline = response.text.strip()

        playbook = {"observed_trades": len(observations), "total_paise": total}
        if board:
            playbook["board"] = [
                {"rank": row["rank"], "campaign": row["campaign"],
                 "movement": row["movement"], "merchants": row["merchants"],
                 "value_paise": row["value_paise"],
                 "needs": list(row.get("needs", [])),
                 "driver": row.get("driver", ""),
                 "discussion": row.get("discussion", ""),
                 "threads": row.get("threads", [])}
                for row in board
            ]
        lot = mint_lot(
            headline=headline,
            playbook=playbook,
            contributor_ids=contributors,
            category="campaign_board" if board else "market",
        )
        self._lots.append(lot)
        self._log.append(
            HOUSE_ACTOR_ID,
            ev.INSIGHT_MINTED,
            {"asset_id": lot.asset_id, "headline": headline, "k": verdict.k,
             "contributor_ids": lot.spec["contributor_ids"]},
            correlation_id=correlation_id,
        )
        return lot

    def feed(self) -> tuple[str, ...]:
        """The free half: headlines only, never a playbook."""
        return tuple(lot.spec["headline"] for lot in self._lots)
