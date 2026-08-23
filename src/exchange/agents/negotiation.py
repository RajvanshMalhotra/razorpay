"""Two brokers haggling, and four reasons it can stop.

A hard round cap makes brokers look like scripts and yields no signal — a
counter tells you that it stopped, never why. Instead:

  reasoning  the agent decides the gap is not worth another round
  progress   the gap between the sides has stopped moving
  backstop   the token budget is exhausted; should never fire
  (the wall clock bounds the whole market run, not a single negotiation)

Progress is measured on the GAP between the two sides, not on each offer.
Oscillation — 1900, 2000, 1900 — moves every offer a lot and closes nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from exchange.llm.base import LLMMessage, LLMProvider

_PRICE = re.compile(r"PRICE:\s*(\d+)", re.IGNORECASE)
_WALK = re.compile(r"\bWALK\b", re.IGNORECASE)

NEGOTIATOR_PROMPT = """You are negotiating a single business-to-business trade.

Reply with ONE of:
  PRICE: <integer in paise> followed by one short sentence of reasoning
  WALK: followed by one short sentence saying why you are ending this

Walk away when the remaining gap is not worth another exchange, when the other
side has stopped moving, or when you have a better option. Do not walk away
merely because the other side is haggling — that is normal.
Never explain your limit. One or two sentences only."""


@dataclass(frozen=True)
class Offer:
    actor_id: str
    price: int
    message: str


@dataclass(frozen=True)
class Outcome:
    agreed: bool
    final_price: int | None
    offers: tuple[Offer, ...]
    ended_reason: str


def parse_offer(text: str) -> tuple[int | None, bool]:
    """Return (price, wants_to_walk). A walk beats a price if both appear."""
    if _WALK.search(text):
        return None, True
    match = _PRICE.search(text)
    return (int(match.group(1)) if match else None), False


def gap_stalled(offers, lookback: int = 2, epsilon: int = 100) -> bool:
    """True when the distance between the two sides has stopped closing.

    Needs at least lookback+1 completed pairs to judge. Measured on the gap,
    never on individual offers — a side can move a lot and concede nothing.
    """
    pairs = []
    for i in range(1, len(offers)):
        if offers[i].actor_id != offers[i - 1].actor_id:
            pairs.append(abs(offers[i].price - offers[i - 1].price))
    if len(pairs) < lookback + 1:
        return False
    recent = pairs[-(lookback + 1):]
    return all(abs(recent[i] - recent[i - 1]) < epsilon for i in range(1, len(recent)))


def negotiate(
    buyer_id: str,
    seller_id: str,
    buyer_provider: LLMProvider,
    seller_provider: LLMProvider,
    opening_price: int,
    buyer_limit: int,
    seller_floor: int,
    token_budget: int = 8000,
) -> Outcome:
    offers: list[Offer] = []
    spent = 0
    transcript: list[str] = [f"Opening ask: {opening_price}"]

    turn = "buyer"
    while True:
        if spent >= token_budget:
            return Outcome(False, None, tuple(offers), "token budget exhausted")

        if turn == "buyer":
            provider, actor, limit_line = (
                buyer_provider, buyer_id, f"You will not pay above {buyer_limit}.",
            )
        else:
            provider, actor, limit_line = (
                seller_provider, seller_id, f"You will not sell below {seller_floor}.",
            )

        response = provider.complete(
            [LLMMessage("user", "\n".join(transcript) + f"\n\n{limit_line}\nYour reply:")],
            system=NEGOTIATOR_PROMPT,
            max_tokens=256,
        )
        spent += response.input_tokens + response.output_tokens

        price, walking = parse_offer(response.text)

        if walking:
            offers.append(Offer(actor, offers[-1].price if offers else opening_price,
                                response.text))
            return Outcome(False, None, tuple(offers), f"{actor} walked away")

        if price is None:
            transcript.append(f"{actor}: {response.text}")
            turn = "seller" if turn == "buyer" else "buyer"
            continue

        offers.append(Offer(actor, price, response.text))
        transcript.append(f"{actor}: {response.text}")

        if (
            len(offers) >= 2
            and offers[-1].actor_id != offers[-2].actor_id
            and offers[-1].price == offers[-2].price
        ):
            return Outcome(True, price, tuple(offers), "agreed")

        if gap_stalled(offers):
            return Outcome(False, None, tuple(offers), "stalled")

        turn = "seller" if turn == "buyer" else "buyer"
