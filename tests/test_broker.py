import pytest

from exchange.agents.broker import Broker
from exchange.eventlog import EventLog
from exchange.models import (
    Actor, ActorKind, ActorStatus, Asset, AssetKind, Currency, Order, Side, Verdict,
)
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex
from exchange.service import Exchange
from exchange.llm.scripted import ScriptedProvider
from tests.test_rails import FakeRazorpay
from tests.test_retrieval import fake_embedder


@pytest.fixture
def exchange(tmp_path):
    log = EventLog(str(tmp_path / "broker.db"))
    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_b", "status": "captured"}]}
    })
    ex = Exchange(log, HybridIndex(embed_fn=fake_embedder),
                  RazorpayRail(log, fake, poll_attempts=1, poll_interval=0),
                  CreditRail(log))
    for actor_id in ("m_buyer", "m_seller"):
        ex.register_actor(Actor(actor_id=actor_id, kind=ActorKind.MERCHANT))
    ex.list_asset(Asset(asset_id="ast_mailers", kind=AssetKind.GOODS,
                        title="biodegradable mailers compostable poly", spec={},
                        currency=Currency.INR, origin_actor_id="m_seller"))
    ex.post_order(Order(order_id="ord_ask", actor_id="m_seller", side=Side.ASK,
                        asset_ref="ast_mailers", asset_query=None, qty=1000,
                        limit_price=1940, currency=Currency.INR,
                        expires_at="2026-09-30T00:00:00+00:00", policy_snapshot={}),
                  correlation_id="c1")
    yield ex
    log.close()


def test_broker_finds_supply_for_a_plain_language_need(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(["looks feasible"]))

    matches = broker.find_supply("biodegradable compostable mailers", 500, 2200, "c1")

    assert matches
    assert matches[0].ask_order_id == "ord_ask"


def test_finding_supply_posts_a_real_bid_to_the_book(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(["looks feasible"]))

    broker.find_supply("biodegradable compostable mailers", 500, 2200, "c1")

    bids = [o for o in exchange.state().open_orders.values() if o.side == Side.BID]
    assert len(bids) == 1
    assert bids[0].is_descriptive is True


def test_the_diplomat_advises_on_a_counterparty(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(["unknown, try small"]))

    assert "try small" in broker.assess("m_seller")


def test_recall_is_injected_before_the_diplomat_speaks(exchange):
    provider = ScriptedProvider(["BEHAVIOURAL: pushes on delivery", "advice here"])
    broker = Broker("m_buyer", exchange, provider)
    from exchange.agents.context import ContextState
    broker.subconscious.consolidate(ContextState(facts=("x",)), "m_seller", "packaging")

    broker.assess("m_seller")

    assert "pushes on delivery" in provider.calls[-1]["messages"][0].content


def test_closing_a_trade_goes_through_the_gate(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    matches = broker.find_supply("biodegradable compostable mailers", 500, 2200, "c1")

    decision, settlement = broker.close(matches[0], "m_seller", "c1")

    assert decision.verdict == Verdict.ALLOW
    assert settlement is not None
    types = [e.type for e in exchange.log.read_by_correlation("c1")]
    assert types.index("POLICY_DECIDED") < types.index("SETTLEMENT_INITIATED")


def test_a_stranger_is_gated_by_confidence_not_excluded(exchange):
    """The broker has never dealt with m_seller, so confidence is 0."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    matches = broker.find_supply("biodegradable compostable mailers", 500, 2200, "c1")

    decision, _ = broker.close(matches[0], "m_seller", "c1")

    assert decision.verdict == Verdict.ALLOW  # 1940 is far under the trial cap
    assert decision.limits_evaluated["counterparty_confidence"] == 0.0


def test_a_closed_trade_updates_the_relationship(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    matches = broker.find_supply("biodegradable compostable mailers", 500, 2200, "c1")

    broker.close(matches[0], "m_seller", "c1")

    assert broker.graph.confidence("m_seller") > 0.0


def test_each_sub_agent_gets_its_own_branch(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(["a", "b"]))

    broker.find_supply("mailers", 500, 2200, "c1")
    broker.assess("m_seller")

    trader_node = broker._trader.node_id
    diplomat_node = broker._diplomat.node_id
    assert trader_node != diplomat_node


def test_a_settled_trade_checkpoints_each_sub_agents_memory(exchange):
    """A completed trade is the episode boundary; without a checkpoint every
    later action re-materialises the whole chain from the root."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    matches = broker.find_supply("biodegradable compostable mailers", 500, 2200, "c1")

    broker.close(matches[0], "m_seller", "c1")

    for agent in (broker._trader, broker._scout, broker._diplomat):
        assert broker.tree.node(agent.node_id).checkpoint is not None
