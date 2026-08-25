"""The intelligence economy, end to end, against fakes.

The properties here are the product's claims: the floor refuses legibly, an
auction clears second-price, points are conserved, and every point that moves
passed the gate first.
"""
import pytest

from exchange.eventlog import EventLog
from exchange.house.accountant import Accountant
from exchange.house.agent import HouseAgent
from exchange.house.insights import HOUSE_ACTOR_ID, K_MIN
from exchange.llm.base import LLMResponse
from exchange.projections import fold
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex
from exchange.service import Exchange
from scripts.market.house_cycle import run_house_cycle
from tests.test_rails import FakeRazorpay
from tests.test_retrieval import fake_embedder


class Says:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        return LLMResponse(text=self.text, input_tokens=5, output_tokens=5,
                           model="fake")


class Bidder:
    """A broker stub that values a lot at a fixed number."""

    def __init__(self, actor_id, amount, reason=None, explode=False):
        self.actor_id = actor_id
        self._amount = amount
        self._reason = reason or f"BID: {amount}"
        self._explode = explode

    def value_insight(self, headline, category, cap):
        from exchange.house.auction import Bid

        if self._explode:
            raise RuntimeError("valuation failed")
        return Bid(actor_id=self.actor_id, amount=min(self._amount, cap),
                   reason=self._reason)


@pytest.fixture
def market(tmp_path):
    log = EventLog(str(tmp_path / "house.db"))
    exchange = Exchange(
        log, HybridIndex(embed_fn=fake_embedder),
        RazorpayRail(log, FakeRazorpay()), CreditRail(log),
    )
    yield exchange
    log.close()


def _settled_trades(exchange, n):
    """n completed INR settlements from n distinct merchants."""
    from exchange import events as ev

    from exchange.policy import GATE_ACTOR_ID

    for i in range(n):
        actor = f"m_{i}"
        sid = f"stl_{i}"
        # A real settlement always has a gate decision before it. Writing one
        # without is what `ungated_settlement` exists to catch, and a fixture
        # that skips it is asserting against a market that cannot happen.
        exchange.log.append(GATE_ACTOR_ID, ev.POLICY_DECIDED, {
            "decision_id": f"d_{i}", "action_ref": f"mch_{i}", "verdict": "ALLOW",
            "reason": "within limits", "limits_evaluated": {}, "ts": "t",
        }, correlation_id=f"c_{i}")
        exchange.log.append(actor, ev.SETTLEMENT_INITIATED, {
            "settlement_id": sid, "match_id": f"mch_{i}", "currency": "INR",
            "amount": 500_000, "razorpay_order_id": f"order_{i}",
            "payment_link_id": f"plink_{i}",
        }, correlation_id=f"c_{i}")
        exchange.log.append(actor, ev.SETTLEMENT_COMPLETED, {
            "settlement_id": sid, "razorpay_payment_id": f"pay_{i}",
        }, correlation_id=f"c_{i}")


def _fund(exchange, actor_id, points):
    """An opening balance, respecting OPENING_GRANT_CAP.

    The cap is 2,000 and it is not a test nuisance: "where do points come
    from?" has to have a bounded answer, and an uncapped grant is the same
    unbounded source a raw log.append was. A merchant that needs more than a
    grant earns it by trading, which is the point of the economy.
    """
    from exchange.house.points import OPENING_GRANT_CAP

    accountant = Accountant(exchange.log, None)
    left = points
    n = 0
    while left > 0:
        chunk = min(left, OPENING_GRANT_CAP)
        accountant.mint(actor_id, chunk, None, correlation_id=f"seed_{actor_id}_{n}",
                        reason="opening balance")
        left -= chunk
        n += 1


# --- the floor ---------------------------------------------------------------

def test_the_cycle_refuses_below_the_privacy_floor(market):
    """And says so legibly. A visible refusal is a working control; a crash
    is not, and the run has to survive reaching this point."""
    _settled_trades(market, K_MIN - 1)
    house = HouseAgent(market.log, Says("cold brew is rising in Bangalore"))

    report = run_house_cycle(market, house, {}, correlation_id="cycle")

    assert report.minted is False
    assert report.refused_reason
    assert str(K_MIN - 1) in report.refused_reason
    assert any(e.type == "PRIVACY_REFUSED" for e in market.log.read_all())


def test_the_refusal_does_not_call_the_model(market):
    """Refusing after paying for a headline would be a floor that costs money
    to enforce."""
    _settled_trades(market, 3)
    provider = Says("a headline")

    run_house_cycle(market, HouseAgent(market.log, provider), {},
                    correlation_id="cycle")

    assert provider.calls == 0


# --- minting and the auction -------------------------------------------------

def test_a_lot_is_minted_once_the_floor_is_cleared(market):
    _settled_trades(market, K_MIN)
    house = HouseAgent(market.log, Says("cold brew concentrate demand is rising"))

    report = run_house_cycle(market, house, {}, correlation_id="cycle")

    assert report.minted
    assert "cold brew" in report.headline


def test_the_auction_clears_at_the_second_price(market):
    """The whole mechanism: the winner pays the runner-up's bid, not its own."""
    _settled_trades(market, K_MIN)
    _fund(market, "m_rich", 5_000)
    house = HouseAgent(market.log, Says("demand is rising"))
    brokers = {
        "m_rich": Bidder("m_rich", 1_800),
        "m_mid": Bidder("m_mid", 1_200),
        "m_low": Bidder("m_low", 400),
    }

    report = run_house_cycle(market, house, brokers, correlation_id="cycle")

    assert report.winner_id == "m_rich"
    assert report.clearing_price == 1_200, "the runner-up's bid, not the winner's"


def test_a_policy_decision_precedes_every_point_that_moves(market):
    """Points convert to fee rebates, so this is a money action and design
    decision 4 is unqualified about them."""
    _settled_trades(market, K_MIN)
    _fund(market, "m_rich", 5_000)
    house = HouseAgent(market.log, Says("demand is rising"))
    brokers = {"m_rich": Bidder("m_rich", 1_800), "m_mid": Bidder("m_mid", 1_200)}

    run_house_cycle(market, house, brokers, correlation_id="cycle")

    events = market.log.read_by_correlation("cycle")
    transferred = [e for e in events if e.type == "CREDITS_TRANSFERRED"]
    assert transferred
    for moved in transferred:
        initiated = [e for e in events
                     if e.type == "SETTLEMENT_INITIATED"
                     and e.payload["settlement_id"] == moved.payload["settlement_id"]][0]
        allow = [e for e in events
                 if e.type == "POLICY_DECIDED"
                 and e.payload["action_ref"] == initiated.payload["match_id"]
                 and e.payload["verdict"] == "ALLOW"][0]
        assert allow.seq < moved.seq


def test_contributors_are_paid_out_of_what_the_house_took_in(market):
    """The royalty is what turns extraction into a deal. It also has to be
    funded: the house starts at zero and can only pay from what it sold."""
    _settled_trades(market, K_MIN)
    _fund(market, "m_rich", 5_000)
    house = HouseAgent(market.log, Says("demand is rising"))
    brokers = {"m_rich": Bidder("m_rich", 1_800), "m_mid": Bidder("m_mid", 1_200)}

    report = run_house_cycle(market, house, brokers, correlation_id="cycle")

    assert report.royalties_paid > 0
    assert report.royalty_each > 0
    balances = fold(market.log.read_all()).credit_balances
    assert balances[HOUSE_ACTOR_ID] >= 0, "the house cannot pay what it never got"


def test_the_whole_cycle_leaves_the_books_clean(market):
    """Points conserved, nothing minted outside the accountant, no ungated
    settlement — asserted over the whole cycle rather than one step."""
    _settled_trades(market, K_MIN)
    _fund(market, "m_rich", 5_000)
    house = HouseAgent(market.log, Says("demand is rising"))
    brokers = {"m_rich": Bidder("m_rich", 1_800), "m_mid": Bidder("m_mid", 1_200)}

    run_house_cycle(market, house, brokers, correlation_id="cycle")

    violations = Accountant(market.log, FakeRazorpay()).assert_invariants()
    assert [v.kind for v in violations] == []


# --- degrading rather than corrupting ---------------------------------------

def test_a_broker_whose_valuation_fails_does_not_stop_the_auction(market):
    """One bad valuation is not the auction's problem."""
    _settled_trades(market, K_MIN)
    _fund(market, "m_rich", 5_000)
    house = HouseAgent(market.log, Says("demand is rising"))
    brokers = {
        "m_rich": Bidder("m_rich", 1_800),
        "m_mid": Bidder("m_mid", 1_200),
        "m_broken": Bidder("m_broken", 0, explode=True),
    }

    report = run_house_cycle(market, house, brokers, correlation_id="cycle")

    assert report.winner_id == "m_rich"
    assert len(report.errors) == 1
    assert "m_broken" in report.errors[0]


def test_an_auction_with_one_readable_bid_does_not_clear(market):
    """Second price needs a second price. A market of one has none, and
    inventing one would be the auction asserting a price nobody offered."""
    _settled_trades(market, K_MIN)
    house = HouseAgent(market.log, Says("demand is rising"))

    report = run_house_cycle(market, house, {"m_only": Bidder("m_only", 900)},
                             correlation_id="cycle")

    assert report.winner_id is None
    assert report.royalties_paid == 0


def test_the_cycle_can_be_re_run_against_the_same_log(market):
    """Tuning means running this repeatedly without re-trading the market."""
    _settled_trades(market, K_MIN)
    _fund(market, "m_rich", 20_000)
    house = HouseAgent(market.log, Says("demand is rising"))
    brokers = {"m_rich": Bidder("m_rich", 1_800), "m_mid": Bidder("m_mid", 1_200)}

    first = run_house_cycle(market, house, brokers, correlation_id="cycle_1")
    second = run_house_cycle(market, house, brokers, correlation_id="cycle_2")

    assert first.minted and second.minted
    assert [v.kind for v in
            Accountant(market.log, FakeRazorpay()).assert_invariants()] == []
