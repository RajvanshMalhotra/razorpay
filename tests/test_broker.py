import pytest

from exchange.agents.broker import Broker
from exchange.eventlog import EventLog
from exchange.models import (
    Actor, ActorKind, ActorStatus, Asset, AssetKind, Currency, Order,
    SettlementStatus, Side, Verdict,
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

    assert "try small" in broker.assess("m_seller", "c1")


def test_recall_is_injected_before_the_diplomat_speaks(exchange):
    provider = ScriptedProvider(["BEHAVIOURAL: pushes on delivery", "advice here"])
    broker = Broker("m_buyer", exchange, provider)
    from exchange.agents.context import ContextState
    broker.subconscious.consolidate(ContextState(facts=("x",)), "m_seller", "packaging")

    broker.assess("m_seller", "c1")

    assert "pushes on delivery" in provider.calls[-1]["messages"][0].content


def test_an_injected_recall_always_lands_on_the_trade(exchange):
    """assess used to journal only if the caller happened to pass a
    correlation_id. A recall that steered a decision and left no trace on the
    trade is exactly the audit-trail claim going unmet, so it is not optional."""
    provider = ScriptedProvider(["BEHAVIOURAL: pushes on delivery", "advice here"])
    broker = Broker("m_buyer", exchange, provider)
    from exchange.agents.context import ContextState
    broker.subconscious.consolidate(ContextState(facts=("x",)), "m_seller", "packaging")

    broker.assess("m_seller", "c1")

    injected = [
        e for e in exchange.log.read_by_correlation("c1")
        if e.type == "RECALL_INJECTED"
    ]
    assert len(injected) == 1
    assert injected[0].payload["counterparty_id"] == "m_seller"
    assert injected[0].payload["lessons"] == ["pushes on delivery"]


def test_assess_requires_a_correlation_id(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(["advice"]))

    with pytest.raises(TypeError):
        broker.assess("m_seller")


def test_closing_a_trade_goes_through_the_gate(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(
        ["ok", "BEHAVIOURAL: moved fast on volume"]))
    # 200 units at 1940 is 388,000, inside the 500,000 trial cap a stranger gets.
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    decision, settlement = broker.close(matches[0], "m_seller", "c1")

    assert decision.verdict == Verdict.ALLOW
    assert settlement is not None
    types = [e.type for e in exchange.log.read_by_correlation("c1")]
    assert types.index("POLICY_DECIDED") < types.index("SETTLEMENT_INITIATED")


def test_a_stranger_is_gated_by_confidence_not_excluded(exchange):
    """The broker has never dealt with m_seller, so confidence is 0.

    The stranger is still matched and still offered — that is the "not
    excluded" half. What binds is the trial-size cap: 500 units at 1940 is
    970,000 of real exposure against a 500,000 unknown-counterparty cap, so a
    lot this size is refused until a track record exists.
    """
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    matches = broker.find_supply("biodegradable compostable mailers", 500, 2200, "c1")
    assert matches, "a stranger must still reach the book"

    decision, _ = broker.close(matches[0], "m_seller", "c1")

    assert decision.verdict == Verdict.DENY
    assert decision.limits_evaluated["amount"] == 970_000
    assert decision.limits_evaluated["counterparty_confidence"] == 0.0
    assert "unknown counterparty cap" in decision.reason


def test_a_closed_trade_updates_the_relationship(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(
        ["ok", "BEHAVIOURAL: moved fast on volume"]))
    # Trial-sized, so the stranger's cap allows it and a deal is actually recorded.
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    broker.close(matches[0], "m_seller", "c1")

    assert broker.graph.confidence("m_seller") > 0.0


def test_each_sub_agent_gets_its_own_branch(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(["a", "b"]))

    broker.find_supply("mailers", 500, 2200, "c1")
    broker.assess("m_seller", "c1")

    trader_node = broker._trader.node_id
    diplomat_node = broker._diplomat.node_id
    assert trader_node != diplomat_node


def test_a_settled_trade_checkpoints_each_sub_agents_memory(exchange):
    """A completed trade is the episode boundary; without a checkpoint every
    later action re-materialises the whole chain from the root."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(
        ["ok", "BEHAVIOURAL: moved fast on volume"]))
    # Trial-sized, so the trade actually settles and the episode boundary lands.
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    broker.close(matches[0], "m_seller", "c1")

    for agent in (broker._trader, broker._scout, broker._diplomat):
        assert broker.tree.node(agent.node_id).checkpoint is not None


def test_an_uncaptured_payment_is_not_remembered_as_a_delivered_deal(exchange):
    """PENDING means nobody paid yet. Trusting a counterparty for it is backwards."""
    exchange._inr_rail = RazorpayRail(exchange.log, FakeRazorpay(payments_by_order={}),
                                      poll_attempts=1, poll_interval=0)
    broker = Broker("m_buyer", exchange, ScriptedProvider(
        ["ok", "BEHAVIOURAL: moved fast on volume"]))
    # Trial-sized, so the gate allows it and the settlement is what stalls.
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    _, settlement = broker.close(matches[0], "m_seller", "c1")

    assert settlement.status == SettlementStatus.PENDING
    assert broker.graph.confidence("m_seller") == 0.0, "no deal was completed"


def test_the_settled_amount_is_the_negotiated_price_not_the_ask(exchange):
    """A trail that records agreeing at one price and paying another is not a trail."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(
        ["ok", "BEHAVIOURAL: moved fast on volume"]))
    # 200 rather than 500: at 500 the lot is over the stranger's trial cap and
    # the gate denies it, so no settlement would exist to inspect.
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")
    assert matches[0].clearing_price == 1940  # the ask

    broker.close(matches[0], "m_seller", "c1", agreed_price=1900)

    initiated = [
        e for e in exchange.log.read_by_correlation("c1")
        if e.type == "SETTLEMENT_INITIATED"
    ][0]
    assert initiated.payload["amount"] == 1900 * 200


def test_a_settled_trade_files_a_lesson_the_broker_can_recall(exchange):
    """Nothing in src/ closed the memory loop: consolidate was called only by
    tests, so LESSON_CONSOLIDATED came from no production path and the second
    deal was informed by the first only when a test did it by hand."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(
        ["ok", "BEHAVIOURAL: folded on the third round, moves fast on volume"]))
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    broker.close(matches[0], "m_seller", "c1")

    assert broker.subconscious.recall("m_seller") == (
        "folded on the third round, moves fast on volume",
    )
    consolidated = [
        e for e in exchange.log.read_by_correlation("c1")
        if e.type == "LESSON_CONSOLIDATED"
    ]
    assert len(consolidated) == 1
    assert consolidated[0].payload["counterparty_id"] == "m_seller"
    assert consolidated[0].payload["kind"] == "behavioural"
    assert consolidated[0].actor_id == "m_buyer"


def test_a_denied_trade_files_no_lesson(exchange):
    """There is nothing to learn about a counterparty we never traded with."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    matches = broker.find_supply("biodegradable compostable mailers", 500, 2200, "c1")

    decision, _ = broker.close(matches[0], "m_seller", "c1")

    assert decision.verdict == Verdict.DENY
    assert broker.subconscious.recall("m_seller") == ()
    assert "LESSON_CONSOLIDATED" not in [
        e.type for e in exchange.log.read_by_correlation("c1")
    ]


def test_a_stranger_is_ranked_optimistically_not_neutrally(exchange):
    """The optimism must survive the trip into the matcher, or the market
    ossifies into cliques and a new merchant never gets a first deal."""
    from exchange.agents.relationships import UNKNOWN_STANDING

    broker = Broker("m_buyer", exchange, ScriptedProvider(
        ["ok", "BEHAVIOURAL: moved fast on volume"]))
    matches = broker.find_supply("biodegradable compostable mailers", 500, 2200, "c1")

    assert matches
    assert f"standing {UNKNOWN_STANDING:.2f}" in matches[0].rationale
