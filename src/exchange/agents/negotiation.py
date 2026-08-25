"""Two brokers haggling, and five reasons it can stop.

A hard round cap makes brokers look like scripts and yields no signal — a
counter tells you that it stopped, never why. So the interesting endings come
first and the counter is only ever a floor:

  reasoning  the agent decides the gap is not worth another round
  progress   the gap between the sides has stopped moving
  budget     the token budget is exhausted; should never fire
  backstop   the call cap is reached; fires only when everything above failed
  (the wall clock bounds the whole market run, not a single negotiation)

Progress is measured on the GAP between the two sides, not on each offer.
Oscillation — 1900, 2000, 1900 — moves every offer a lot and closes nothing.

WHY A CALL CAP EXISTS AT ALL, given that the design argued against one. Every
bound above it is a bound the PROVIDER gets to supply or defeat:

- `spent` accumulates `input_tokens + output_tokens`, and `openai_compat`
  reports 0 for both when the response carries no `usage` block. A provider
  that omits usage — a gateway, a proxy, a streaming path — makes the token
  budget a counter that never advances. Measured against the merged source:
  5,000 model calls without termination.
- An unparseable reply appends no offer, so `gap_stalled` cannot fire on that
  path either, and the agent's own reasoning cannot end a conversation it never
  managed to join. 67 calls for one degenerate negotiation with usage present.

A bound that the party it constrains supplies is the defect this project keeps
rediscovering; here the constrained party is the paid API. So the call cap is
counted HERE, on calls this loop actually made, from a figure nothing outside
this function contributes to. It is a floor under the token budget rather than
a replacement for it: the budget still ends a wordy negotiation sooner, and the
cap ends a silent one at all.

A zero-token response is charged `_ASSUMED_TOKENS` rather than nothing, for the
same reason: a call that reports no cost still costs money, and "free" is the
one thing it certainly was not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from exchange.llm.base import LLMMessage, LLMProvider

_PRICE = re.compile(r"PRICE:\s*(\d+)", re.IGNORECASE)
_WALK = re.compile(r"\bWALK\b", re.IGNORECASE)

# Twelve model calls: six turns each. A healthy negotiation in this suite ends
# in two to six calls, the audit's worst degenerate case ran to 67, and the
# whole 30-merchant run budgets 700-900 calls — so twelve is comfortably above
# any negotiation worth having and far below one that can eat the run's budget
# on its own. It is a backstop, not a design parameter: if it starts firing in
# healthy runs the fault is upstream, in the prompt or the model tier.
MAX_MODEL_CALLS = 12

# What a call is charged when the provider reports no usage. Roughly a
# negotiation turn under the 256-token reply cap; the exact figure matters less
# than that it is not zero.
_ASSUMED_TOKENS = 500

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
    journal=None,
    max_calls: int = MAX_MODEL_CALLS,
) -> Outcome:
    offers: list[Offer] = []
    spent = 0
    transcript: list[str] = [f"Opening ask: {opening_price}"]

    if journal:
        journal.negotiation_opened(seller_id, opening_price)

    # NEGOTIATION_OPENED is already written. A provider that raises mid-loop
    # would otherwise leave an opening with no ending in the log — a story that
    # stops mid-sentence and no way to tell it from one still in progress.
    try:
        return _rounds(
            buyer_id, seller_id, buyer_provider, seller_provider,
            opening_price, buyer_limit, seller_floor, token_budget, journal,
            offers, spent, transcript, max_calls,
        )
    except Exception as exc:
        if journal:
            journal.negotiation_ended(False, None, f"error: {type(exc).__name__}")
        raise


def _rounds(
    buyer_id: str,
    seller_id: str,
    buyer_provider: LLMProvider,
    seller_provider: LLMProvider,
    opening_price: int,
    buyer_limit: int,
    seller_floor: int,
    token_budget: int,
    journal,
    offers: list[Offer],
    spent: int,
    transcript: list[str],
    max_calls: int = MAX_MODEL_CALLS,
) -> Outcome:
    turn = "buyer"
    calls = 0
    while True:
        if spent >= token_budget:
            if journal:
                journal.negotiation_ended(False, None, "token budget exhausted")
            return Outcome(False, None, tuple(offers), "token budget exhausted")

        # The one bound nothing outside this loop can defeat. An ending, not an
        # error: a negotiation that ran out of patience is an ordinary market
        # outcome and it goes into the log in the same shape as a walk-away, so
        # a replay of the trade shows why it stopped rather than showing an
        # opening with nothing after it.
        if calls >= max_calls:
            reason = f"call limit reached ({max_calls} model calls)"
            if journal:
                journal.negotiation_ended(False, None, reason)
            return Outcome(False, None, tuple(offers), reason)

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
            # MEASURED, not guessed. deepseek-v4-pro spends its budget on
            # reasoning BEFORE emitting a character: at max_tokens=256 with
            # the default effort it returned 256 reasoning tokens and an
            # EMPTY string, on every single call. At 800 with effort="low"
            # it spends ~320 reasoning tokens and answers properly.
            #
            # effort="none" also answers, and answers WORSE — it walked away
            # from a deal at 31000 when its own ceiling was 31000. Reasoning
            # is what makes the negotiation a negotiation, so the budget is
            # sized to afford it rather than switched off to save it.
            max_tokens=800,
            reasoning_effort="low",
        )
        # Counted before anything can `continue` past it, and counted whatever
        # the reply turns out to be: an unparseable answer is a paid call.
        calls += 1
        reported = response.input_tokens + response.output_tokens
        spent += reported if reported > 0 else _ASSUMED_TOKENS

        price, walking = parse_offer(response.text)

        if walking:
            offers.append(Offer(actor, offers[-1].price if offers else opening_price,
                                response.text))
            if journal:
                journal.negotiation_ended(False, None, f"{actor} walked away")
            return Outcome(False, None, tuple(offers), f"{actor} walked away")

        if price is None:
            transcript.append(f"{actor}: {response.text}")
            turn = "seller" if turn == "buyer" else "buyer"
            continue

        offers.append(Offer(actor, price, response.text))
        transcript.append(f"{actor}: {response.text}")
        if journal:
            journal.negotiation_round(actor, price, response.text)

        if (
            len(offers) >= 2
            and offers[-1].actor_id != offers[-2].actor_id
            and offers[-1].price == offers[-2].price
        ):
            if journal:
                journal.negotiation_ended(True, price, "agreed")
            return Outcome(True, price, tuple(offers), "agreed")

        if gap_stalled(offers):
            if journal:
                journal.negotiation_ended(False, None, "stalled")
            return Outcome(False, None, tuple(offers), "stalled")

        turn = "seller" if turn == "buyer" else "buyer"
