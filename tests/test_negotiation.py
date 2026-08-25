import pytest

from exchange.agents.negotiation import (
    MAX_MODEL_CALLS,
    Offer,
    gap_stalled,
    negotiate,
    parse_offer,
)
from exchange.llm.base import LLMResponse
from exchange.llm.scripted import ScriptedProvider


def test_parse_offer_reads_a_price():
    price, walk = parse_offer("I can do PRICE: 1940 on those terms.")

    assert price == 1940
    assert walk is False


def test_parse_offer_detects_walking_away():
    price, walk = parse_offer("WALK: we are too far apart on delivery.")

    assert walk is True


def test_parse_offer_returns_none_when_there_is_no_price():
    price, walk = parse_offer("Tell me more about the volumes first.")

    assert price is None
    assert walk is False


def test_gap_stalled_is_false_while_the_sides_are_closing():
    offers = [
        Offer("buyer", 1800, ""), Offer("seller", 2200, ""),
        Offer("buyer", 1900, ""), Offer("seller", 2000, ""),
    ]

    assert gap_stalled(offers) is False


def test_gap_stalled_is_true_when_the_gap_stops_moving():
    offers = [
        Offer("buyer", 1900, ""), Offer("seller", 2000, ""),
        Offer("buyer", 1901, ""), Offer("seller", 1999, ""),
        Offer("buyer", 1902, ""), Offer("seller", 1998, ""),
    ]

    assert gap_stalled(offers, epsilon=100) is True


def test_gap_stalled_sees_through_oscillation():
    """Each side moves a lot every round; the gap between them does not.
    Non-crossing on purpose: two different actors naming the same price is an
    agreement, so a crossing oscillation can never reach the stall check."""
    offers = [
        Offer("buyer", 1900, ""), Offer("seller", 2100, ""),
        Offer("buyer", 1950, ""), Offer("seller", 2050, ""),
        Offer("buyer", 1900, ""), Offer("seller", 2100, ""),
    ]

    assert gap_stalled(offers) is True


def test_negotiation_agrees_when_the_seller_accepts():
    buyer = ScriptedProvider(["PRICE: 1900 — that is my offer."])
    seller = ScriptedProvider(["PRICE: 1900 — agreed."])

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2000, buyer_limit=2200, seller_floor=1800)

    assert outcome.agreed is True
    assert outcome.final_price == 1900
    assert outcome.ended_reason == "agreed"


def test_an_agent_can_walk_away_and_the_reason_is_kept():
    buyer = ScriptedProvider(["WALK: the gap is not worth another round."])
    seller = ScriptedProvider(["PRICE: 2100"])

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2100, buyer_limit=2200, seller_floor=1800)

    assert outcome.agreed is False
    assert "walked" in outcome.ended_reason
    assert "not worth another round" in outcome.offers[-1].message


def test_a_stalled_negotiation_ends_without_agreement():
    buyer = ScriptedProvider(["PRICE: 1900", "PRICE: 1901", "PRICE: 1902", "PRICE: 1903"])
    seller = ScriptedProvider(["PRICE: 2000", "PRICE: 1999", "PRICE: 1998", "PRICE: 1997"])

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2000, buyer_limit=2200, seller_floor=1800)

    assert outcome.agreed is False
    assert outcome.ended_reason == "stalled"


def test_the_token_budget_backstops_a_runaway():
    """Should never fire in a healthy run. If it does, it is a bug upstream."""
    buyer = ScriptedProvider(["PRICE: 1900"] * 50)
    seller = ScriptedProvider(["PRICE: 2100"] * 50)

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2000, buyer_limit=2200, seller_floor=1800,
                        token_budget=200)

    assert outcome.agreed is False
    assert outcome.ended_reason == "token budget exhausted"


def test_a_reply_without_a_price_cannot_manufacture_an_agreement():
    """One side saying something unpriced must not let the other agree with itself.

    The buyer names 1900, the seller replies without a price, the buyer names
    1900 again. Two consecutive same-actor offers at the same price is exactly
    the shape that used to read as a meeting of minds — the agreement check
    compares the last two offers, and before the fix the unpriced reply did not
    interrupt them. It must now sit in the transcript and agree nothing.
    """
    buyer = ScriptedProvider(["PRICE: 1900 our offer", "PRICE: 1900 again"])
    seller = ScriptedProvider(["Tell me more about the volumes first.",
                               "WALK: too much back and forth"])

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2000, buyer_limit=2200, seller_floor=1800)

    consecutive_same_actor_same_price = [
        (outcome.offers[i - 1], outcome.offers[i])
        for i in range(1, len(outcome.offers))
        if outcome.offers[i].actor_id == outcome.offers[i - 1].actor_id
        and outcome.offers[i].price == outcome.offers[i - 1].price
    ]
    assert consecutive_same_actor_same_price, "the situation under test must occur"
    assert consecutive_same_actor_same_price[0][0].actor_id == "m_buyer"
    assert consecutive_same_actor_same_price[0][0].price == 1900
    assert outcome.agreed is False, "the seller never named a price"


def test_a_provider_failure_still_ends_the_negotiation_in_the_log():
    """NEGOTIATION_OPENED is written before the loop. An exception mid-loop must
    not leave an opening with no ending — a story that stops mid-sentence."""
    class Exploding:
        def complete(self, messages, *, system=None, max_tokens=1024,
                     reasoning_effort=None):
            raise RuntimeError("the model fell over")

    class RecordingJournal:
        def __init__(self):
            self.entries = []

        def negotiation_opened(self, counterparty_id, opening_price):
            self.entries.append(("opened", counterparty_id, opening_price))

        def negotiation_round(self, actor_id, price, message):
            self.entries.append(("round", actor_id, price))

        def negotiation_ended(self, agreed, final_price, reason):
            self.entries.append(("ended", agreed, final_price, reason))

    journal = RecordingJournal()

    with pytest.raises(RuntimeError, match="fell over"):
        negotiate("m_buyer", "m_seller", Exploding(), Exploding(),
                  opening_price=2000, buyer_limit=2200, seller_floor=1800,
                  journal=journal)

    assert journal.entries[0][0] == "opened"
    assert journal.entries[-1] == ("ended", False, None, "error: RuntimeError")


# --- the hard call cap: the one bound the provider cannot defeat ------------
#
# `spent` accumulates `input_tokens + output_tokens`, and `openai_compat`
# reports 0 for both when the response carries no `usage` block — a gateway, a
# proxy, a streaming path. With that field absent the token budget was a
# counter that never advanced, and an unparseable reply appends no offer, so
# `gap_stalled` could not fire either. Measured against the pre-fix source:
# 5,000 model calls without terminating, on a paid API.
#
# So these tests are about money. They pin the cap that stops a market run
# spending without a ceiling, and they must keep failing if it is ever removed.


class ZeroUsageProvider:
    """A provider that never converges AND reports no usage at all.

    HONEST ABOUT REPORTING NOTHING: `input_tokens` and `output_tokens` are
    literally 0, which is exactly what `openai_compat` produces from a
    response with no `usage` block. A stub that quietly reported a token or
    two would let the token budget end the loop and the test would pass
    without the cap existing — the "fake kinder than the real thing" failure
    this project has already paid for twice.

    `limit` is a tripwire, not a bound under test: it exists so that a build
    WITHOUT the cap fails the test in finite time instead of hanging forever.
    It is set far above `MAX_MODEL_CALLS`, so a correct loop never reaches it.
    """

    def __init__(self, text: str, limit: int = 200) -> None:
        self.text = text
        self.limit = limit
        self.calls = 0

    def complete(self, messages, *, system=None, max_tokens=1024,
                 reasoning_effort=None):
        self.calls += 1
        if self.calls > self.limit:
            raise AssertionError(
                f"runaway negotiation: {self.calls} model calls with no "
                "termination — the call cap is missing or not being counted"
            )
        return LLMResponse(
            text=self.text, input_tokens=0, output_tokens=0, model="zero-usage",
        )


def test_the_stub_really_does_report_zero_usage():
    """Guards the two tests below. If this provider ever charged tokens they
    would pass on the token budget rather than on the call cap, and the thing
    under test would be untested."""
    response = ZeroUsageProvider("no price here").complete([])

    assert response.input_tokens == 0
    assert response.output_tokens == 0


def test_a_provider_that_reports_no_tokens_still_terminates():
    """The audit's runaway, bounded. Unparseable replies append no offer, so
    neither the stall check nor the agents' own reasoning can end this; with
    zero-token responses the budget never advances either. Only a cap counted
    on calls THIS LOOP made can stop it."""
    buyer = ZeroUsageProvider("thinking about it")
    seller = ZeroUsageProvider("let me consider")

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2000, buyer_limit=2200, seller_floor=1800)

    assert buyer.calls + seller.calls == MAX_MODEL_CALLS
    assert outcome.ended_reason == f"call limit reached ({MAX_MODEL_CALLS} model calls)"


def test_the_cap_ends_the_negotiation_as_a_result_not_as_an_exception():
    """A negotiation that ran out of patience is an ordinary market outcome and
    goes into the log in the same shape as a walk-away. Never an exception —
    no caller in the repo catches one — and never silence, which would leave a
    NEGOTIATION_OPENED with nothing after it."""
    buyer = ZeroUsageProvider("still thinking")
    seller = ZeroUsageProvider("still considering")

    class RecordingJournal:
        def __init__(self):
            self.entries = []

        def negotiation_opened(self, counterparty_id, opening_price):
            self.entries.append(("opened", counterparty_id, opening_price))

        def negotiation_round(self, actor_id, price, message):
            self.entries.append(("round", actor_id, price))

        def negotiation_ended(self, agreed, final_price, reason):
            self.entries.append(("ended", agreed, final_price, reason))

    journal = RecordingJournal()

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2000, buyer_limit=2200, seller_floor=1800,
                        journal=journal)

    # The walk-away shape: no agreement, no price, and a reason that says why.
    assert outcome.agreed is False
    assert outcome.final_price is None
    assert "call limit reached" in outcome.ended_reason
    # Recorded, not merely returned.
    assert journal.entries[0][0] == "opened"
    assert journal.entries[-1] == ("ended", False, None, outcome.ended_reason)


def test_the_cap_is_counted_on_calls_not_on_a_figure_the_provider_supplies():
    """The whole point of the fix. The previous bound was the provider's own
    token report; this one is a count of calls this loop made, which nothing
    outside the loop contributes to."""
    buyer = ZeroUsageProvider("no price")
    seller = ZeroUsageProvider("no price either")

    negotiate("m_buyer", "m_seller", buyer, seller,
              opening_price=2000, buyer_limit=2200, seller_floor=1800,
              max_calls=4)

    assert buyer.calls + seller.calls == 4


def test_a_healthy_negotiation_is_untouched_by_the_cap():
    """The cap is a backstop, not a design parameter. A normal haggle ends on
    agreement in a handful of calls and never sees it — if this starts failing,
    the fault is upstream in the prompt or the model tier."""
    buyer = ScriptedProvider(["PRICE: 1900 — that is my offer."])
    seller = ScriptedProvider(["PRICE: 1900 — agreed."])

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2000, buyer_limit=2200, seller_floor=1800)

    assert outcome.agreed is True
    assert outcome.final_price == 1900
    assert outcome.ended_reason == "agreed"
    assert len(buyer.calls) + len(seller.calls) < MAX_MODEL_CALLS


def test_a_zero_token_call_is_charged_rather_than_treated_as_free():
    """A call that reports no cost still costs money, and 'free' is the one
    thing it certainly was not. With every response reporting zero usage, a
    small budget must still be exhausted — otherwise the budget is decorative
    whenever the provider omits `usage`."""
    buyer = ZeroUsageProvider("no price")
    seller = ZeroUsageProvider("no price either")

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2000, buyer_limit=2200, seller_floor=1800,
                        token_budget=1000)

    assert outcome.ended_reason == "token budget exhausted"
    assert buyer.calls + seller.calls < MAX_MODEL_CALLS, (
        "the budget must bind before the cap here, or this proves nothing"
    )
