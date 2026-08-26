"""One round of the market: every merchant with a need tries to meet it.

THE BUDGET IS A POLICY GATE FOR MODEL SPEND, and it is here for the same
reason the exchange has one for money. Four fix waves went into making every
money action bounded and gated; a runner that can spend without a ceiling is
the same defect in a different currency. `Budget` is checked BEFORE each
merchant's turn, and exhausting it ends the round cleanly with a resumable log
rather than raising.

NOTHING HERE MAY KILL THE RUN. Thirty brokers over two hours against a real
model and a real payment gateway will produce timeouts, malformed replies,
rate limits and refusals. Each is one merchant's bad turn, recorded and
stepped over — not the market's problem. A run that dies loses hours of real
spend, and the log it leaves has to still be worth resuming.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from exchange import events as ev
from exchange.agents.journal import AgentJournal
from exchange.agents.negotiation import negotiate
from exchange.matching import resize

_log = logging.getLogger(__name__)

# What a merchant will pay for intelligence, and what it opens a negotiation
# at relative to the ask. Both are starting points the agent reasons from,
# never multipliers applied to its judgment.
OPENING_DISCOUNT_BPS = 1200   # open 12% under the ask

# How many merchants act at once. Not "as many as possible": every worker is
# an open connection to a paid API with its own rate limit, and a runaway
# fan-out turns one slow round into a wall of 429s. Eight is comfortably
# inside DeepSeek's concurrency and turns a 90-second turn into a 90-second
# round of eight.
DEFAULT_CONCURRENCY = 8


@dataclass
class Budget:
    """A ceiling on the run, checked before each turn.

    Wall clock as well as calls, because the two fail differently: a slow
    provider burns the afternoon without burning the budget, and a cheap fast
    one burns the budget without burning the afternoon.
    """
    max_turns: int = 10_000
    max_seconds: float = 3_600.0
    turns_used: int = 0
    _started: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def exhausted(self) -> str | None:
        """The reason this budget is spent, or None."""
        if self.turns_used >= self.max_turns:
            return f"turn budget spent ({self.max_turns} turns)"
        if self.elapsed >= self.max_seconds:
            return f"time budget spent ({self.max_seconds:.0f}s)"
        return None

    def spend_turn(self) -> None:
        self.turns_used += 1


@dataclass(frozen=True)
class TurnResult:
    actor_id: str
    need: str
    outcome: str              # settled | walked | denied | no_supply | error
    detail: str = ""
    correlation_id: str | None = None
    amount: int | None = None


@dataclass
class RoundReport:
    round_no: int
    turns: list[TurnResult] = field(default_factory=list)
    stopped_early: str | None = None

    def count(self, outcome: str) -> int:
        return sum(1 for t in self.turns if t.outcome == outcome)

    def __str__(self) -> str:
        settled = self.count("settled")
        moved = sum(t.amount or 0 for t in self.turns if t.outcome == "settled")
        parts = [
            f"round {self.round_no}: {len(self.turns)} turns",
            f"{settled} settled ({moved / 100:,.0f} rupees)",
            f"{self.count('walked')} walked",
            f"{self.count('denied')} denied",
            f"{self.count('no_supply')} found nothing",
            f"{self.count('error')} errored",
        ]
        if self.stopped_early:
            parts.append(f"STOPPED: {self.stopped_early}")
        return ", ".join(parts)


def _already_traded(log, actor_id: str, need_text: str, round_no: int) -> bool:
    """Has this merchant already FINISHED this turn?

    Finished, not started. The first version asked whether the turn's thread
    held any event at all, and a killed process leaves turns holding exactly
    one — the bid they posted before dying. On resume all fifteen of those
    were counted as done and never ran, so 18 of 32 merchants traded and the
    privacy floor could not clear.

    ORDER_POSTED cannot answer this on its own: it is also all a turn leaves
    behind when it searched and found no supply, which IS finished. So the
    runner records its own outcome and resumption reads that.
    """
    marker = turn_correlation(actor_id, need_text, round_no)
    return any(
        e.type == ev.TURN_ENDED and e.payload.get("outcome") != "error"
        for e in log.read_by_correlation(marker)
    )


def turn_correlation(actor_id: str, need_text: str, round_no: int) -> str:
    """One turn, one thread — and derivable, so a resumed run can find it.

    A random id per turn would make resumption impossible: there would be no
    way to ask "did this merchant already do this?" without a second source of
    truth beside the log.
    """
    slug = "".join(c if c.isalnum() else "_" for c in need_text.lower())[:40]
    return f"turn_{round_no}_{actor_id}_{slug}"


def _is_a_size_refusal(reason: str) -> bool:
    """Was this refused for being too big, rather than for being wrong?

    Only a size refusal is worth retrying smaller. A frozen actor, a
    self-signed permit or a price above our own posted limit are all refusals
    that a smaller quantity does not answer, and retrying them would burn a
    model call to be told the same thing.

    Matching on "exceeds" alone is not enough, and the near-miss is
    instructive: a price above our own posted ceiling reads "Agreed price
    30000 exceeds the limit 1800 posted on bid ord_1" — it contains
    "exceeds", it is not about size, and shrinking the lot does not change
    the per-unit price by a paisa. So the test is for the CAP being the thing
    exceeded, which is the only refusal a smaller quantity can answer.
    """
    lowered = reason.lower()
    if "posted on bid" in lowered:
        return False
    return "cap" in lowered or "rolling window" in lowered


def _affordable_qty(decision, unit_price: int) -> int | None:
    """The largest quantity that fits every cap the gate just evaluated.

    Read out of the decision's own `limits_evaluated`, not guessed and not
    passed in: the gate is the authority on what it allows, and a caller that
    invents a trial size is one more instance of the defect this project keeps
    finding — the constrained party choosing its own bound.

    `unit_price` MUST be the price that will actually be charged, which is the
    AGREED price and not the match's `clearing_price`. Sizing against the ask
    while `close()` charges the negotiated figure put the retry just over the
    line whenever the negotiation landed above the ask:

        qty 900 at 1764 -> 1,587,600, over the 500,000 cap
        retry qty 285   ->   502,740, over it again by 2,740

    A bound computed from a different number than the one being bounded is the
    same defect as the freeze that read a status nobody set.
    """
    limits = getattr(decision, "limits_evaluated", None) or {}
    ceilings = [
        int(value)
        for key, value in limits.items()
        if isinstance(value, (int, float))
        and value > 0
        and ("cap" in key or "threshold" in key)
    ]
    if not ceilings or unit_price <= 0:
        return None
    return min(ceilings) // unit_price


def _attempt_turn(exchange, broker, merchant, need, round_no, budget) -> TurnResult:
    """One merchant, one need, start to finish. Never raises."""
    correlation_id = turn_correlation(merchant.actor_id, need.text, round_no)

    try:
        matches = broker.find_supply(
            need_text=need.text,
            qty=need.qty,
            limit_price=need.limit_price,
            correlation_id=correlation_id,
        )
        if not matches:
            return TurnResult(merchant.actor_id, need.text, "no_supply",
                              correlation_id=correlation_id)

        match = broker.choose(matches, correlation_id=correlation_id)
        posted = exchange.state().posted_orders.get(match.ask_order_id)
        seller_id = posted.actor_id if posted else "unknown"

        # `assess` is NOT called here, and dropping it was worth 24 seconds a
        # turn. `choose` is already a Diplomat call, and it is already given
        # each candidate's recalled history — so calling `assess` immediately
        # afterwards asks the same agent about the same counterparty a second
        # time, and its answer lands in the log after the choice it was
        # supposed to inform.
        #
        # It stays on `Broker` for the single-counterparty case, where there
        # is no shortlist to choose from and the Diplomat's read is the only
        # judgment on offer.
        if len(matches) == 1:
            broker.assess(seller_id, correlation_id=correlation_id)

        opening = match.clearing_price - (
            match.clearing_price * OPENING_DISCOUNT_BPS // 10_000
        )
        outcome = negotiate(
            buyer_id=merchant.actor_id,
            seller_id=seller_id,
            buyer_provider=broker.fast_tier,
            seller_provider=broker.fast_tier,
            opening_price=match.clearing_price,
            buyer_limit=need.limit_price,
            seller_floor=opening,
            journal=AgentJournal(exchange.log, merchant.actor_id, correlation_id),
        )
        if not outcome.agreed or outcome.final_price is None:
            return TurnResult(merchant.actor_id, need.text, "walked",
                              detail=outcome.ended_reason,
                              correlation_id=correlation_id)

        decision, settlement = broker.close(
            match=match,
            seller_id=seller_id,
            correlation_id=correlation_id,
            agreed_price=outcome.final_price,
        )

        # THE TRIAL TRADE. A first dealing with a stranger is capped at
        # `unknown_counterparty_cap`, so a full lot is refused by design —
        # that is the anti-incumbency mechanism, not a failure. The merchant's
        # options are to walk or to try smaller, and trying smaller is what
        # earns the track record that lifts the cap.
        #
        # `resize`, never `replace`: the retry is a SECOND action on different
        # terms and must reach the gate with its own match_id, or the DENY and
        # the later ALLOW share one action_ref and the accountant's join
        # cannot tell which one the money moved on.
        if settlement is None and _is_a_size_refusal(decision.reason):
            trial = _affordable_qty(decision, outcome.final_price)
            if trial and trial < match.qty:
                smaller = resize(match, trial)
                decision, settlement = broker.close(
                    match=smaller,
                    seller_id=seller_id,
                    correlation_id=correlation_id,
                    agreed_price=outcome.final_price,
                )
                if settlement is not None:
                    return TurnResult(
                        merchant.actor_id, need.text, "settled",
                        detail=(f"{settlement.status} at {outcome.final_price}"
                                f"/unit, trial size {trial} of {match.qty}"),
                        correlation_id=correlation_id,
                        amount=settlement.amount,
                    )

        if settlement is None:
            return TurnResult(merchant.actor_id, need.text, "denied",
                              detail=decision.reason,
                              correlation_id=correlation_id)
        return TurnResult(
            merchant.actor_id, need.text, "settled",
            detail=f"{settlement.status} at {outcome.final_price}/unit",
            correlation_id=correlation_id,
            amount=settlement.amount,
        )
    except Exception as exc:  # noqa: BLE001 - one bad turn is not the market's problem
        # Recorded, stepped over, and the run continues. With 30 brokers over
        # two hours something WILL fail; losing the whole run to it would cost
        # hours of real spend.
        _log.exception("turn failed for %s", merchant.actor_id)
        return TurnResult(merchant.actor_id, need.text, "error",
                          detail=f"{type(exc).__name__}: {exc}",
                          correlation_id=correlation_id)


def run_round(exchange, brokers, merchants, round_no, budget,
              concurrency: int = DEFAULT_CONCURRENCY) -> RoundReport:
    """Every merchant with a need this round.

    CONCURRENTLY, because a turn is almost entirely spent waiting on a model.
    Measured on one real turn: 196 seconds, of which 195 were API latency and
    1.3 were Razorpay. Run serially, 55 turns is an hour and a half of a
    machine doing nothing. The turns are independent — different merchants,
    different needs — so the only shared thing is the log, which takes a lock.

    Order is no longer roster order, and that is fine: the market has no
    turn order to respect, each turn threads its own correlation id, and the
    log records the order things actually happened rather than the order a
    loop happened to visit merchants.
    """
    report = RoundReport(round_no=round_no)

    pending = []
    for merchant in merchants:
        for need in merchant.needs:
            if need.round_no != round_no:
                continue
            if _already_traded(exchange.log, merchant.actor_id,
                               need.text, round_no):
                continue
            broker = brokers.get(merchant.actor_id)
            if broker is None:
                continue
            spent = budget.exhausted()
            if spent:
                report.stopped_early = spent
                break
            budget.spend_turn()
            pending.append((broker, merchant, need))
        if report.stopped_early:
            break

    if not pending:
        return report

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [
            pool.submit(run_turn, exchange, broker, merchant, need,
                        round_no, budget)
            for broker, merchant, need in pending
        ]
        for future in as_completed(futures):
            report.turns.append(future.result())

    return report


def run_turn(exchange, broker, merchant, need, round_no, budget) -> TurnResult:
    """Take the turn, and RECORD THAT IT ENDED, whatever the outcome.

    The marker is written for EVERY outcome, errors included, because the log
    should record that the turn was attempted and how it went.

    But an error does not COUNT as finished, and the first version had that
    wrong. The reasoning was "a turn that errored has been paid for and is not
    improved by running it again" — true of a model that answered badly, false
    of one that never answered: five merchants lost their turns to
    `APIConnectionError: Connection error`, a network blip, and were then
    permanently skipped as though they had traded.

    A market outcome is settled, walked, denied or no_supply. An error is not
    a market outcome; it is the market failing to happen. The budget still
    bounds how often a persistently broken turn can be retried.
    """
    result = _attempt_turn(exchange, broker, merchant, need, round_no, budget)
    exchange.log.append(
        merchant.actor_id,
        ev.TURN_ENDED,
        {
            "round": round_no,
            "need": need.text,
            "outcome": result.outcome,
            "detail": result.detail,
            "amount": result.amount,
        },
        correlation_id=result.correlation_id or turn_correlation(
            merchant.actor_id, need.text, round_no),
    )
    return result
