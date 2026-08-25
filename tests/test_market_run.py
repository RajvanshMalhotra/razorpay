"""What has to hold for a two-hour paid run to be worth starting.

Every test here injects a scripted provider and a fake Razorpay client. The
run these protect spends real money against a real gateway; none of that
belongs in a test.
"""
import pytest

from exchange.eventlog import EventLog
from exchange.llm.base import LLMResponse
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex
from exchange.service import Exchange
from scripts.market.roster import Listing, Merchant, Need
from scripts.market.run import Budget, run_round, run_turn, turn_correlation
from scripts.market.seed import seed
from tests.test_rails import FakeRazorpay
from tests.test_retrieval import fake_embedder


class ScriptedProvider:
    """Replies from a list, cycling. Counts every call it is asked for."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0

    def complete(self, messages, **kwargs):
        reply = self._replies[self.calls % len(self._replies)]
        self.calls += 1
        return LLMResponse(text=reply, input_tokens=10, output_tokens=10,
                           model="scripted")


class ExplodingProvider:
    def complete(self, messages, **kwargs):
        raise RuntimeError("provider is down")


SELLER = Merchant(
    actor_id="m_seller", name="Seller", category="supply",
    persona="sells things",
    sells=(Listing("ast_widget", "biodegradable widgets industrial",
                   {"kind": "widget"}, 2000, 5000),),
    needs=(),
)
BUYER = Merchant(
    actor_id="m_buyer", name="Buyer", category="demand",
    persona="buys things",
    sells=(),
    needs=(Need(1, "biodegradable widgets industrial", 100, 2500),),
)


@pytest.fixture
def market(tmp_path):
    log = EventLog(str(tmp_path / "run.db"))
    exchange = Exchange(
        log, HybridIndex(embed_fn=fake_embedder),
        RazorpayRail(log, FakeRazorpay()), CreditRail(log),
    )
    seed(exchange, (SELLER, BUYER))
    yield exchange
    log.close()


def _broker(exchange, provider):
    from exchange.agents.broker import Broker

    return Broker("m_buyer", exchange, provider, fast_provider=provider)


# --- the budget is a gate on model spend ------------------------------------

def test_an_exhausted_budget_stops_the_round_cleanly(market):
    """Not an exception, and not a half-written turn. The log must still be
    resumable, because the operator's next move is to raise the budget and
    run the same command again."""
    budget = Budget(max_turns=0)
    provider = ScriptedProvider(["PRICE: 2000"])

    report = run_round(market, {"m_buyer": _broker(market, provider)},
                       (BUYER,), round_no=1, budget=budget)

    assert report.stopped_early
    assert "turn budget" in report.stopped_early
    assert report.turns == []
    assert provider.calls == 0, "no model was called after the budget was spent"


def test_the_budget_is_checked_before_the_turn_not_after(market):
    """Checking afterwards means always overspending by one turn — which on
    a strong-tier model is not a rounding error."""
    budget = Budget(max_turns=1)
    provider = ScriptedProvider(["PRICE: 2000"])
    broker = _broker(market, provider)

    run_round(market, {"m_buyer": broker}, (BUYER,), round_no=1, budget=budget)

    assert budget.turns_used == 1


def test_a_time_budget_stops_the_round(market):
    """A slow provider burns the afternoon without burning the turn budget."""
    budget = Budget(max_seconds=0.0)

    report = run_round(market, {"m_buyer": _broker(market, ScriptedProvider(["x"]))},
                       (BUYER,), round_no=1, budget=budget)

    assert report.stopped_early
    assert "time budget" in report.stopped_early


# --- nothing may kill the run -----------------------------------------------

def test_a_broker_whose_provider_dies_does_not_stop_the_round(market):
    """One merchant's bad turn is not the market's problem. Over two hours
    with thirty brokers, something will fail."""
    brokers = {"m_buyer": _broker(market, ExplodingProvider())}

    report = run_round(market, brokers, (BUYER,), round_no=1, budget=Budget())

    assert len(report.turns) == 1
    assert report.turns[0].outcome == "error"
    assert "provider is down" in report.turns[0].detail
    assert report.stopped_early is None


def test_a_failed_turn_still_names_its_thread(market):
    """So the post-mortem can find what the merchant did before it broke."""
    brokers = {"m_buyer": _broker(market, ExplodingProvider())}

    report = run_round(market, brokers, (BUYER,), round_no=1, budget=Budget())

    assert report.turns[0].correlation_id == turn_correlation(
        "m_buyer", BUYER.needs[0].text, 1,
    )


# --- resumption --------------------------------------------------------------

def test_a_turn_already_taken_is_not_taken_again(market):
    """Resumption after an interrupt must not re-trade a round: every turn
    costs model spend and may move real money."""
    provider = ScriptedProvider(["PRICE: 2000"])
    brokers = {"m_buyer": _broker(market, provider)}

    run_round(market, brokers, (BUYER,), round_no=1, budget=Budget())
    calls_after_first = provider.calls
    second = run_round(market, brokers, (BUYER,), round_no=1, budget=Budget())

    assert second.turns == []
    assert provider.calls == calls_after_first, "no model was called on resume"


def test_the_turn_marker_is_derived_not_random(market):
    """A random id per turn would make resumption impossible: there would be
    no way to ask the log whether a turn had already happened."""
    first = turn_correlation("m_a", "some need", 2)
    again = turn_correlation("m_a", "some need", 2)
    different_round = turn_correlation("m_a", "some need", 3)

    assert first == again
    assert first != different_round


def test_a_different_round_is_a_different_turn(market):
    """The same merchant wanting the same thing again next round is a new
    trade, not a repeat of the old one."""
    provider = ScriptedProvider(["PRICE: 2000"])
    brokers = {"m_buyer": _broker(market, provider)}
    buyer_round_2 = Merchant(
        actor_id="m_buyer", name="Buyer", category="demand", persona="p",
        sells=(), needs=(Need(2, BUYER.needs[0].text, 100, 2500),),
    )

    run_round(market, brokers, (BUYER,), round_no=1, budget=Budget())
    second = run_round(market, brokers, (buyer_round_2,), round_no=2,
                       budget=Budget())

    assert len(second.turns) == 1


# --- what the round reports --------------------------------------------------

def test_a_walked_negotiation_is_reported_as_such(market):
    """A market where every negotiation succeeds has nothing to watch, so a
    walk is a normal outcome the report has to carry."""
    provider = ScriptedProvider(["WALK: too expensive for us"])
    brokers = {"m_buyer": _broker(market, provider)}

    report = run_round(market, brokers, (BUYER,), round_no=1, budget=Budget())

    assert report.turns[0].outcome == "walked"


def test_a_merchant_with_no_need_this_round_takes_no_turn(market):
    provider = ScriptedProvider(["PRICE: 2000"])
    brokers = {"m_buyer": _broker(market, provider)}

    report = run_round(market, brokers, (BUYER,), round_no=4, budget=Budget())

    assert report.turns == []
    assert provider.calls == 0


# --- the trial trade ---------------------------------------------------------
#
# A first dealing with a stranger is capped, so a full lot is refused BY
# DESIGN — that is the anti-incumbency mechanism, not a failure. Trying
# smaller is what earns the track record that lifts the cap.


def test_a_lot_too_big_for_a_stranger_is_retried_smaller(market):
    """The headline behaviour: refused at full size, settled at trial size."""
    provider = ScriptedProvider(["PRICE: 2000"])
    big_need = Merchant(
        actor_id="m_buyer", name="Buyer", category="demand", persona="p",
        sells=(),
        needs=(Need(1, "biodegradable widgets industrial", 5000, 2500),),
    )
    brokers = {"m_buyer": _broker(market, provider)}

    report = run_round(market, brokers, (big_need,), round_no=1,
                       budget=Budget())

    assert report.turns[0].outcome == "settled"
    assert "trial size" in report.turns[0].detail


def test_the_retry_is_a_new_match_not_the_same_one_again(market):
    """A DENY and a later ALLOW must never share an action_ref: the accountant
    joins settlements to decisions on match_id precisely so a refused match
    and an allowed one on the same thread cannot be confused."""
    provider = ScriptedProvider(["PRICE: 2000"])
    big_need = Merchant(
        actor_id="m_buyer", name="Buyer", category="demand", persona="p",
        sells=(),
        needs=(Need(1, "biodegradable widgets industrial", 5000, 2500),),
    )

    run_round(market, {"m_buyer": _broker(market, provider)}, (big_need,),
              round_no=1, budget=Budget())

    decisions = [e for e in market.log.read_all() if e.type == "POLICY_DECIDED"]
    verdicts = {d.payload["action_ref"]: d.payload["verdict"] for d in decisions}
    assert "DENY" in verdicts.values()
    assert "ALLOW" in verdicts.values()
    assert len(verdicts) >= 2, "the retry reached the gate under its own id"


def test_the_whole_round_leaves_no_ungated_settlement(market):
    """The invariant the project is judged on, asserted over a whole round."""
    from exchange.house.accountant import Accountant

    provider = ScriptedProvider(["PRICE: 2000"])
    big_need = Merchant(
        actor_id="m_buyer", name="Buyer", category="demand", persona="p",
        sells=(),
        needs=(Need(1, "biodegradable widgets industrial", 5000, 2500),),
    )

    run_round(market, {"m_buyer": _broker(market, provider)}, (big_need,),
              round_no=1, budget=Budget())

    violations = Accountant(market.log, FakeRazorpay()).assert_invariants()
    assert [v.kind for v in violations] == []


def test_a_refusal_that_a_smaller_size_cannot_answer_is_not_retried(market):
    """A price above our own posted limit stays refused however small the
    lot; retrying would burn a model call to be told the same thing."""
    from scripts.market.run import _is_a_size_refusal

    # The near-miss that matters: this one CONTAINS "exceeds" but shrinking
    # the lot does not change the per-unit price by a paisa.
    assert not _is_a_size_refusal(
        "Agreed price 30000 exceeds the limit 1800 posted on bid ord_1"
    )
    assert not _is_a_size_refusal("Actor is frozen pending reconciliation")
    assert not _is_a_size_refusal("m_a wrote its own ALLOW for mch_1")
    assert not _is_a_size_refusal(
        "Amount 0 is not a payment; a settlement must move money"
    )

    # And the two that a smaller lot genuinely answers.
    assert _is_a_size_refusal("Amount 3000000 exceeds per-transaction cap 2000000")
    assert _is_a_size_refusal(
        "Amount 900000 exceeds unknown counterparty cap 500000 at confidence 0.00"
    )
