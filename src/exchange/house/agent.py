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

    def mint_from(self, observations: list[dict], correlation_id: str):
        """Turn observations into a lot, or refuse and say why.

        The refusal is logged as loudly as the success — a floor nobody can
        see is indistinguishable from no floor.
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
        response = self._provider.complete(
            [LLMMessage(
                "user",
                f"{len(observations)} settled trades across {verdict.k} merchants, "
                f"totalling {total} paise. Write the headline.",
            )],
            system=HEADLINE_PROMPT,
            # 120 was not a tight budget, it was an empty one: this model
            # spent all of it reasoning and returned "". The headline is what
            # appears on screen, so it gets room to think (~529 tokens) and
            # then room to answer.
            max_tokens=800,
            reasoning_effort="low",
        )
        headline = response.text.strip()

        lot = mint_lot(
            headline=headline,
            playbook={"observed_trades": len(observations), "total_paise": total},
            contributor_ids=contributors,
            category="market",
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
