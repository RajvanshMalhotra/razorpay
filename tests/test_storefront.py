"""A person can drive the exchange, and the gate still decides.

The storefront is not a second product: it writes a descriptive bid to the
same order book, runs the same retrieval, and settles through the same gate.
The tests that matter are the ones proving a human cannot do anything an
agent could not.
"""
import pytest

from exchange.eventlog import EventLog
from exchange.models import ActorStatus
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex
from exchange.service import Exchange
from scripts.market.roster import Listing, Merchant, Need
from scripts.market.seed import seed
from scripts.market.storefront import shop
from tests.test_market_run import ScriptedProvider
from tests.test_rails import FakeRazorpay
from tests.test_retrieval import fake_embedder

SELLER = Merchant(
    actor_id="m_seller", name="Seller", category="supply", persona="sells",
    sells=(Listing("ast_mailers", "biodegradable mailers compostable poly",
                   {"material": "compostable"}, 2000, 5000),),
    needs=(Need(1, "biodegradable mailers compostable poly", 10, 2100),),
    style="fair",
)


@pytest.fixture
def market(tmp_path):
    log = EventLog(str(tmp_path / "shop.db"))
    exchange = Exchange(log, HybridIndex(embed_fn=fake_embedder),
                        RazorpayRail(log, FakeRazorpay()), CreditRail(log))
    seed(exchange, (SELLER,))
    yield exchange
    log.close()


def _broker(exchange, provider, actor_id="m_human"):
    from exchange.agents.broker import Broker

    return Broker(actor_id, exchange, provider, fast_provider=provider)


def test_a_person_can_buy_in_plain_language(market):
    provider = ScriptedProvider(["PRICE: 2000 that works"])

    _, result, settlement = shop(
        market, _broker(market, provider),
        "biodegradable mailers", qty=100, limit_price=2200,
        confirm=lambda *a: True,
    )

    assert result == "settled"
    assert settlement is not None


def test_declining_writes_nothing(market):
    """A refusal ends it before anything is committed, because nothing
    happened — there is no half-purchase to record."""
    provider = ScriptedProvider(["PRICE: 2000 fine"])

    _, result, settlement = shop(
        market, _broker(market, provider),
        "biodegradable mailers", qty=100, limit_price=2200,
        confirm=lambda *a: False,
    )

    assert result == "you declined"
    assert settlement is None
    types = [e.type for e in market.log.read_all()]
    assert "SETTLEMENT_INITIATED" not in types


def test_the_gate_fires_for_a_human_too(market):
    """Every money action emits a PolicyDecision before it happens. A
    storefront that skipped it would be a second, ungated way in."""
    provider = ScriptedProvider(["PRICE: 2000 fine"])

    correlation_id, _, _ = shop(
        market, _broker(market, provider),
        "biodegradable mailers", qty=100, limit_price=2200,
        confirm=lambda *a: True,
    )

    thread = market.log.read_by_correlation(correlation_id)
    decisions = [e for e in thread if e.type == "POLICY_DECIDED"]
    settlements = [e for e in thread if e.type == "SETTLEMENT_INITIATED"]
    assert decisions
    assert settlements
    assert decisions[0].seq < settlements[0].seq


def test_human_approval_is_consent_not_permission(market):
    """The person says yes; the gate still decides. A frozen buyer is refused
    however enthusiastically its owner confirms — approval and permission are
    different things, and only one of them is the human's to give."""
    from exchange.house.accountant import Accountant
    from exchange.models import Actor, ActorKind
    from exchange.projections import fold

    market.register_actor(Actor(actor_id="m_human", kind=ActorKind.MERCHANT))
    Accountant(market.log, FakeRazorpay()).freeze("m_human", "books disagree")
    provider = ScriptedProvider(["PRICE: 2000 fine"])

    _, result, settlement = shop(
        market, _broker(market, provider),
        "biodegradable mailers", qty=100, limit_price=2200,
        confirm=lambda *a: True,          # the human insists
    )

    assert settlement is None
    assert "refused" in result
    assert fold(market.log.read_all()).actors["m_human"].status is ActorStatus.FROZEN


def test_the_whole_purchase_is_one_thread(market):
    """A person should be able to follow their own purchase the same way a
    judge follows an agent's trade."""
    provider = ScriptedProvider(["PRICE: 2000 fine"])

    correlation_id, _, _ = shop(
        market, _broker(market, provider),
        "biodegradable mailers", qty=100, limit_price=2200,
        confirm=lambda *a: True,
    )

    story = [e.type for e in market.log.read_by_correlation(correlation_id)]
    assert "ORDER_POSTED" in story
    assert "POLICY_DECIDED" in story
    assert "SETTLEMENT_INITIATED" in story
