import pytest

from exchange.agents.broker import Broker
from exchange.eventlog import EventLog
from exchange.house.accountant import Accountant
from exchange.matching import resize
from exchange.models import (
    Actor, ActorKind, ActorStatus, Asset, AssetKind, Currency, Match, Order,
    SettlementStatus, Side, Verdict,
)
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex
from exchange.service import Exchange
from exchange.llm.base import LLMResponse
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


def test_the_traders_summary_is_promoted_into_the_brokers_own_context(exchange):
    """Spec 4.2: each sub-agent's summary becomes a fact in the orchestrator's
    delta. find_supply discarded the Trader's reply, so the root node's context
    never grew and the one-way narrowing was narrowing into nothing."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["merchant m_seller quotes best"]))
    before = broker.tree.materialise(broker.root_id)
    assert before.facts == ()

    broker.find_supply("biodegradable compostable mailers", 500, 2200, "c1")
    # The summary is promoted off the critical path — the shortlist comes back
    # before the model has finished narrating it — so a test that asserts the
    # promotion waits for it. What it must not do is stop asserting it.
    broker.settle_context()

    after = broker.tree.materialise(broker.root_id)
    assert "merchant m_seller quotes best" in after.facts
    # Narrowing, not merging: the objective the root started with is still there.
    assert after.objective == before.objective


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

    decision, settlement = broker.close(
        matches[0], "m_seller", "c1", agreed_price=matches[0].clearing_price)

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

    decision, _ = broker.close(matches[0], "m_seller", "c1", agreed_price=matches[0].clearing_price)

    assert decision.verdict == Verdict.DENY
    assert decision.limits_evaluated["amount"] == 970_000
    assert decision.limits_evaluated["counterparty_confidence"] == 0.0
    assert "unknown counterparty cap" in decision.reason


def test_a_closed_trade_updates_the_relationship(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(
        ["ok", "BEHAVIOURAL: moved fast on volume"]))
    # Trial-sized, so the stranger's cap allows it and a deal is actually recorded.
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    broker.close(matches[0], "m_seller", "c1", agreed_price=matches[0].clearing_price)

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

    broker.close(matches[0], "m_seller", "c1", agreed_price=matches[0].clearing_price)

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

    _, settlement = broker.close(matches[0], "m_seller", "c1", agreed_price=matches[0].clearing_price)

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

    broker.close(matches[0], "m_seller", "c1", agreed_price=matches[0].clearing_price)

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

    decision, _ = broker.close(matches[0], "m_seller", "c1", agreed_price=matches[0].clearing_price)

    assert decision.verdict == Verdict.DENY
    assert broker.subconscious.recall("m_seller") == ()
    assert "LESSON_CONSOLIDATED" not in [
        e.type for e in exchange.log.read_by_correlation("c1")
    ]


def test_a_stranger_still_reaches_the_shortlist(exchange):
    """Reputation no longer touches ranking — it reaches the choosing agent as
    a fact instead. What must not change is that a merchant nobody has dealt
    with is still offered, or the market ossifies into cliques."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))

    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    assert matches, "a never-dealt-with counterparty must still be a candidate"
    assert broker.graph.confidence("m_seller") == 0.0, "and still be a stranger"


def test_a_consolidation_failure_does_not_lose_a_paid_trade(exchange):
    """Consolidation is a model call and it runs after the money has moved.
    Losing the lesson is recoverable; losing the settlement result is not."""

    class ExplodingProvider:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, *, system=None, max_tokens=1024,
                     reasoning_effort=None):
            self.calls += 1
            if self.calls == 1:  # the Trader's find_supply call
                return LLMResponse("ok", 1, 1, "boom")
            raise RuntimeError("provider timed out")

    broker = Broker("m_buyer", exchange, ExplodingProvider())
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    decision, settlement = broker.close(
        matches[0], "m_seller", "c1", agreed_price=matches[0].clearing_price)

    assert settlement is not None, "the trade was paid for; close() must return it"
    assert settlement.status == SettlementStatus.COMPLETED
    assert broker.graph.confidence("m_seller") > 0.0, "the deal itself was recorded"
    assert broker.subconscious.recall("m_seller") == (), "no lesson survived, as expected"


def test_every_sub_agent_summary_reaches_the_orchestrator(exchange):
    """Spec 4.2: each sub-agent narrows upward. Only the Trader's did."""
    broker = Broker("m_buyer", exchange,
                    ScriptedProvider(["trader says supply is tight",
                                      "diplomat says try them small"]))

    broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")
    # SETTLE BEFORE THE NEXT CALL, because this provider hands out scripted
    # replies in order and the promotion now runs on its own thread — leave it
    # racing and the Trader's line can arrive after the Diplomat's. Nothing in
    # production shares a provider's turn order like this; a test that does
    # has to say when it wants the first call finished.
    broker.settle_context()
    broker.assess("m_seller", "c1")

    root = broker.tree.materialise(broker.root_id)
    assert any("supply is tight" in f for f in root.facts)
    assert any("try them small" in f for f in root.facts)


def test_the_promoted_chain_is_visible_to_the_sub_agents(exchange):
    """A promoted fact the sub-agents cannot see is not shared context."""
    broker = Broker("m_buyer", exchange,
                    ScriptedProvider(["supply is tight", "second call"]))
    broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")
    broker.settle_context()

    trader_sees = broker.tree.materialise(broker._trader.node_id)

    assert any("supply is tight" in f for f in trader_sees.facts)


def test_close_requires_the_negotiated_price(exchange):
    """Optional meant a caller could silently settle at the ask again."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    with pytest.raises(TypeError):
        broker.close(matches[0], "m_seller", "c1")


def test_the_agent_picks_from_the_shortlist_and_says_why(exchange):
    """A shortlist of two is a real choice. The agent makes it, and the reason
    it gives is what lands in the audit trail — not a score."""
    exchange.register_actor(Actor(actor_id="m_rival", kind=ActorKind.MERCHANT))
    exchange.post_order(Order(
        order_id="ord_rival", actor_id="m_rival", side=Side.ASK,
        asset_ref="ast_mailers", asset_query=None, qty=1000, limit_price=1800,
        currency=Currency.INR, expires_at="2026-12-31T00:00:00+00:00",
        policy_snapshot={},
    ), correlation_id="c1")

    broker = Broker("m_buyer", exchange,
                    ScriptedProvider(["trader ok", "I pick 1: never missed a delivery"]))
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")
    assert len(matches) >= 2, "this test needs a real choice to exercise"

    chosen = broker.choose(matches, "c1")

    assert chosen in matches
    events = [e for e in exchange.log.read_by_correlation("c1")
              if e.type == "COUNTERPARTY_CHOSEN"]
    assert len(events) == 1
    assert "never missed a delivery" in events[0].payload["reason"]


def test_an_unparseable_choice_falls_back_to_the_top_of_the_shortlist(exchange):
    """A model that will not answer must not stop the market."""
    exchange.register_actor(Actor(actor_id="m_rival", kind=ActorKind.MERCHANT))
    exchange.post_order(Order(
        order_id="ord_rival", actor_id="m_rival", side=Side.ASK,
        asset_ref="ast_mailers", asset_query=None, qty=1000, limit_price=1800,
        currency=Currency.INR, expires_at="2026-12-31T00:00:00+00:00",
        policy_snapshot={},
    ), correlation_id="c1")

    broker = Broker("m_buyer", exchange,
                    ScriptedProvider(["trader ok", "I have no opinion whatsoever"]))
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")
    assert len(matches) >= 2, "this test needs a real choice to exercise"

    chosen = broker.choose(matches, "c1")

    assert chosen is matches[0]


def test_choosing_from_one_candidate_makes_no_model_call(exchange):
    """A shortlist of one is not a choice."""
    provider = ScriptedProvider(["trader ok"])
    broker = Broker("m_buyer", exchange, provider)
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")
    before = len(provider.calls)

    broker.choose(matches[:1], "c1")

    assert len(provider.calls) == before


def test_the_scout_values_a_lot_and_says_why(exchange):
    broker = Broker("m_buyer", exchange,
                    ScriptedProvider(["BID: 1850 we spend 40k a month in this category"]))

    bid = broker.value_insight("skincare AOV up 12%", "skincare", cap=50_000)

    assert bid.amount == 1850
    assert "40k a month" in bid.reason
    assert bid.actor_id == "m_buyer"


def test_a_valuation_above_the_cap_is_clamped_not_refused(exchange):
    """Judgment picks the number; the cap decides what is allowed. An agent
    that wants more than it may spend still bids — at the limit."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["BID: 999999 worth everything"]))

    bid = broker.value_insight("skincare AOV up 12%", "skincare", cap=50_000)

    assert bid.amount == 50_000


def test_an_unparseable_valuation_is_not_a_bid_at_all(exchange):
    """Silence is not a bid — and it must not be spelled like one.

    `amount == 0` alone was indistinguishable from an agent that genuinely
    valued the lot at nothing, and it counted toward the auction's two-bid
    minimum. `parsed` is what carries the difference into `clear()`.
    """
    broker = Broker("m_buyer", exchange, ScriptedProvider(["I am not sure about this"]))

    bid = broker.value_insight("skincare AOV up 12%", "skincare", cap=50_000)

    assert bid.parsed is False
    assert bid.amount == 0
    assert bid.reason == "I am not sure about this", "the reply is still on record"


def test_a_readable_valuation_is_marked_as_one(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(["BID: 900 worth a look"]))

    assert broker.value_insight("skincare AOV up 12%", "skincare", cap=50_000).parsed


# --- the negotiated price against the merchant's own posted ceiling --------
#
# `_posted_bid_limit`, `_refuse_above_posted_limit` and `_log_refusal` are the
# whole of the answer to "a long haggle can end above the number this merchant
# published on the book". Every other close() in this suite passes
# `agreed_price=matches[0].clearing_price`, which is the ask limit and therefore
# always at or below the bid limit — so the refusal branch ran nowhere, and an
# untested claim on the money path is one review away from being a false one.


def test_a_negotiated_price_above_the_posted_bid_limit_is_refused(exchange):
    """Refused, not clamped: settling at a price neither side agreed to would
    leave a SETTLEMENT_INITIATED that matches neither the negotiation nor the
    ask, with no event explaining the difference."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    decision, settlement = broker.close(matches[0], "m_seller", "c1", agreed_price=2500)

    assert decision.verdict == Verdict.DENY
    assert settlement is None


def test_a_price_above_the_posted_limit_moves_no_money(exchange):
    """The refusal is only worth anything if nothing downstream runs."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    broker.close(matches[0], "m_seller", "c1", agreed_price=2500)

    types = [e.type for e in exchange.log.read_by_correlation("c1")]
    assert "SETTLEMENT_INITIATED" not in types
    assert "SETTLEMENT_COMPLETED" not in types
    # execute_match is never reached, so the match never reaches the gate either.
    assert "MATCH_PROPOSED" not in types
    assert broker.graph.confidence("m_seller") == 0.0


def test_the_refusal_is_logged_as_a_deny_naming_both_figures(exchange):
    """In the gate's vocabulary, on the trade's own thread: a replay shows one
    kind of record for "a money action was refused and here is why", whether the
    bound was the exchange's cap or this merchant's own posted ceiling."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")
    bid_id = [o for o in exchange.state().open_orders.values()
              if o.side == Side.BID][0].order_id

    decision, _ = broker.close(matches[0], "m_seller", "c1", agreed_price=2500)

    decided = [e for e in exchange.log.read_by_correlation("c1")
               if e.type == "POLICY_DECIDED"]
    assert len(decided) == 1
    assert decided[0].payload["verdict"] == "DENY"
    assert decided[0].payload["action_ref"] == matches[0].match_id
    assert decided[0].actor_id == "m_buyer", "the merchant refused itself"
    assert "2500" in decided[0].payload["reason"]
    assert "2200" in decided[0].payload["reason"]
    assert decided[0].payload["limits_evaluated"] == {
        "agreed_price": 2500,
        "bid_limit_price": 2200,
        "bid_order_id": bid_id,
        "qty": matches[0].qty,
    }
    assert decision.limits_evaluated["bid_limit_price"] == 2200


def test_a_price_exactly_at_the_posted_limit_is_allowed(exchange):
    """The ceiling is a ceiling, not a strict inequality — a merchant that
    published 2200 may pay 2200."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(
        ["ok", "BEHAVIOURAL: held firm at our limit"]))
    # 200 units at 2200 is 440,000, inside the 500,000 trial cap a stranger gets.
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    decision, settlement = broker.close(matches[0], "m_seller", "c1", agreed_price=2200)

    assert decision.verdict == Verdict.ALLOW
    assert settlement.status == SettlementStatus.COMPLETED
    initiated = [e for e in exchange.log.read_by_correlation("c1")
                 if e.type == "SETTLEMENT_INITIATED"][0]
    assert initiated.payload["amount"] == 2200 * 200


def test_a_bid_that_is_not_in_the_log_is_refused_rather_than_unbounded(exchange):
    """An unfindable ceiling is not an absent one. Defaulting to "unbounded"
    would make this check advisory for exactly the caller that skipped the
    book — which is the caller it exists to bind."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    match = Match(match_id="mch_offbook", bid_order_id="ord_never_posted",
                  ask_order_id="ord_ask", clearing_price=1940, qty=200,
                  score=0.9, rationale="off the book")

    decision, settlement = broker.close(match, "m_seller", "c1", agreed_price=1)

    assert decision.verdict == Verdict.DENY
    assert settlement is None
    assert "ord_never_posted" in decision.reason
    assert "not in the log" in decision.reason
    assert decision.limits_evaluated["bid_limit_price"] is None
    types = [e.type for e in exchange.log.read_by_correlation("c1")]
    assert "SETTLEMENT_INITIATED" not in types


def test_the_ceiling_is_read_from_the_log_after_the_bid_leaves_the_book(exchange):
    """`state().open_orders` loses a bid once it is filled; the limit it was
    posted under still binds the trade it was posted for."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(
        ["ok", "BEHAVIOURAL: moved fast", "ok"]))
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")
    bid_id = matches[0].bid_order_id

    broker.close(matches[0], "m_seller", "c1", agreed_price=1900)
    assert bid_id not in exchange.state().open_orders, "the bid is filled and gone"

    assert broker._posted_bid_limit(bid_id) == 2200


def test_the_most_recently_posted_limit_is_the_one_that_binds(exchange):
    """Order ids collide across runs against a persistent log. Taking the FIRST
    match read the ceiling off whichever run got there earliest; `fold` resolves
    a repost by overwriting the book, and this now agrees with it."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    for limit in (5000, 2200):
        exchange.post_order(Order(
            order_id="ord_reused", actor_id="m_buyer", side=Side.BID,
            asset_ref=None, asset_query={"text": "mailers"}, qty=200,
            limit_price=limit, currency=Currency.INR,
            expires_at="2026-12-31T00:00:00+00:00", policy_snapshot={},
        ), correlation_id="c1")
    match = Match(match_id="mch_reused", bid_order_id="ord_reused",
                  ask_order_id="ord_ask", clearing_price=1940, qty=200,
                  score=0.9, rationale="test")

    decision, settlement = broker.close(match, "m_seller", "c1", agreed_price=2400)

    assert broker._posted_bid_limit("ord_reused") == 2200
    assert decision.verdict == Verdict.DENY, "the ceiling in force is the later one"
    assert settlement is None


# --- whose bid is it, though ----------------------------------------------
#
# The eight tests above are good tests and every one of them passes a
# `bid_order_id` equal to the broker's own posted bid, so not one of them asks
# whose order the row belongs to. `_posted_bid_limit` did not ask either: it
# read `limit_price` off whatever ORDER_POSTED the id landed on, which is the
# project's recurring defect — a value the checker must be authoritative about,
# supplied by the party it constrains — reached through the check written to
# prevent it.


def _post_bid(exchange, order_id, actor_id, limit_price, qty=50):
    exchange.post_order(Order(
        order_id=order_id, actor_id=actor_id, side=Side.BID,
        asset_ref=None, asset_query={"text": "mailers"}, qty=qty,
        limit_price=limit_price, currency=Currency.INR,
        expires_at="2026-12-31T00:00:00+00:00", policy_snapshot={},
    ), correlation_id="c1")


def test_the_audit_exploit_is_refused(exchange):
    """The audit's scenario verbatim.

    The buyer publishes a BID at 2000 — 50 units, a ceiling of 100,000 paise —
    and then hands the gate a Match whose `bid_order_id` names the SELLER's ASK
    at 9000. The ceiling read back was 9000, the verdict was ALLOW, 250,000
    paise settled against a published maximum of 100,000, and
    `assert_invariants()` returned [].
    """
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    _post_bid(exchange, "ord_own_bid", "m_buyer", limit_price=2000)
    exchange.post_order(Order(
        order_id="ord_seller_ask", actor_id="m_seller", side=Side.ASK,
        asset_ref="ast_mailers", asset_query=None, qty=1000, limit_price=9000,
        currency=Currency.INR, expires_at="2026-12-31T00:00:00+00:00",
        policy_snapshot={},
    ), correlation_id="c1")
    exploit = Match(match_id="mch_exploit", bid_order_id="ord_seller_ask",
                    ask_order_id="ord_seller_ask", clearing_price=5000, qty=50,
                    score=0.9, rationale="the seller's own ask, named as our bid")

    decision, settlement = broker.close(exploit, "m_seller", "c1", agreed_price=5000)

    assert decision.verdict == Verdict.DENY
    assert settlement is None
    types = [e.type for e in exchange.log.read_by_correlation("c1")]
    assert "SETTLEMENT_INITIATED" not in types
    assert "MATCH_PROPOSED" not in types


def test_another_actors_bid_is_not_this_buyers_ceiling(exchange):
    """A ceiling somebody else published binds somebody else."""
    exchange.register_actor(Actor(actor_id="m_rival", kind=ActorKind.MERCHANT))
    _post_bid(exchange, "ord_rival_bid", "m_rival", limit_price=9000)
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    match = Match(match_id="mch_borrowed", bid_order_id="ord_rival_bid",
                  ask_order_id="ord_ask", clearing_price=5000, qty=50,
                  score=0.9, rationale="a ceiling we never published")

    decision, settlement = broker.close(match, "m_seller", "c1", agreed_price=5000)

    assert decision.verdict == Verdict.DENY
    assert settlement is None
    assert "m_rival" in decision.reason
    assert decision.limits_evaluated["bid_limit_price"] is None
    assert broker._posted_bid_limit("ord_rival_bid") is None


def test_an_order_that_is_not_a_bid_cannot_supply_a_ceiling(exchange):
    """An ask's limit is a floor. Reading it as a spending ceiling inverts it."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    match = Match(match_id="mch_askasbid", bid_order_id="ord_ask",
                  ask_order_id="ord_ask", clearing_price=1900, qty=50,
                  score=0.9, rationale="an ask named as the bid")

    decision, settlement = broker.close(match, "m_seller", "c1", agreed_price=1900)

    assert decision.verdict == Verdict.DENY
    assert settlement is None
    assert "not a BID" in decision.reason
    assert broker._posted_bid_limit("ord_ask") is None


def test_the_merchants_own_bid_is_still_accepted_as_its_ceiling(exchange):
    """The checks refuse other people's rows, not this merchant's own."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    _post_bid(exchange, "ord_own_bid", "m_buyer", limit_price=2000)

    assert broker._posted_bid_limit("ord_own_bid") == 2000


def test_reading_the_ceiling_does_not_scan_the_whole_log(exchange):
    """The last full-log scan on the money path, and the one every test in
    test_projection_cost.py missed: it runs inside `Broker.close`, ahead of
    `execute_match`, and that harness calls `execute_match` directly."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    _post_bid(exchange, "ord_own_bid", "m_buyer", limit_price=2000)
    exchange.state()  # the one-off warm fold

    reads = []
    original = exchange.log.read_all
    exchange.log.read_all = lambda: (reads.append(1), original())[1]
    try:
        assert broker._posted_bid_limit("ord_own_bid") == 2000
    finally:
        exchange.log.read_all = original

    assert reads == [], "the ceiling comes from the projection, not from a scan"


# --- one decision per match_id, from the merchant's side too ----------------


def test_a_merchant_cannot_write_a_second_refusal_for_one_match(exchange):
    """`execute_match` refuses a second trip through the gate because
    `assert_invariants` joins settlements to decisions on match_id. A merchant
    writing unlimited POLICY_DECIDED under one action_ref manufactures that
    ambiguity from the other side."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    broker.close(matches[0], "m_seller", "c1", agreed_price=2500)
    with pytest.raises(ValueError, match="fresh match_id"):
        broker.close(matches[0], "m_seller", "c1", agreed_price=2500)

    decided = [e for e in exchange.log.read_by_correlation("c1")
               if e.type == "POLICY_DECIDED"]
    assert len(decided) == 1


def test_a_corrected_retry_is_a_new_match_and_is_decided_on_its_own_terms(exchange):
    """The refusal spends the match_id; `matching.resize` is how a retry gets
    a fresh one, exactly as it does for a gate DENY."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(
        ["ok", "BEHAVIOURAL: held firm"]))
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")
    broker.close(matches[0], "m_seller", "c1", agreed_price=2500)

    decision, settlement = broker.close(
        resize(matches[0], 200), "m_seller", "c1", agreed_price=2200)

    assert decision.verdict == Verdict.ALLOW
    assert settlement is not None


# --- a shortlist entry that left the book ----------------------------------


def test_choosing_survives_a_candidate_leaving_the_book_mid_turn(exchange):
    """The shortlist comes from a snapshot; `choose` used to index the LIVE
    book. With 30 merchants on one log another fill or expiry between the two
    calls is routine, and it was an unrecorded KeyError that killed the turn."""
    exchange.register_actor(Actor(actor_id="m_rival", kind=ActorKind.MERCHANT))
    exchange.post_order(Order(
        order_id="ord_rival", actor_id="m_rival", side=Side.ASK,
        asset_ref="ast_mailers", asset_query=None, qty=1000, limit_price=1800,
        currency=Currency.INR, expires_at="2026-12-31T00:00:00+00:00",
        policy_snapshot={},
    ), correlation_id="c1")
    broker = Broker("m_buyer", exchange,
                    ScriptedProvider(["trader ok", "I pick 1"]))
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")
    assert len(matches) >= 2

    # Another merchant fills the top candidate out of the book mid-turn.
    exchange.log.append("m_rival", "ORDER_FILLED",
                        {"order_id": matches[0].ask_order_id, "qty": 1000},
                        correlation_id="c_other")
    assert matches[0].ask_order_id not in exchange.state().open_orders

    chosen = broker.choose(matches, "c1")

    assert chosen in matches


def test_a_candidate_that_left_the_book_is_named_as_such_to_the_diplomat(exchange):
    """Named rather than silently dropped: whether to pursue an ask that has
    just gone is a judgment, and the agent cannot make it unseen."""
    exchange.register_actor(Actor(actor_id="m_rival", kind=ActorKind.MERCHANT))
    exchange.post_order(Order(
        order_id="ord_rival", actor_id="m_rival", side=Side.ASK,
        asset_ref="ast_mailers", asset_query=None, qty=1000, limit_price=1800,
        currency=Currency.INR, expires_at="2026-12-31T00:00:00+00:00",
        policy_snapshot={},
    ), correlation_id="c1")
    provider = ScriptedProvider(["trader ok", "I pick 2"])
    broker = Broker("m_buyer", exchange, provider)
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")
    exchange.log.append("m_rival", "ORDER_EXPIRED",
                        {"order_id": matches[0].ask_order_id},
                        correlation_id="c_other")

    broker.choose(matches, "c1")

    sent = provider.calls[-1]["messages"][0].content
    assert "NO LONGER IN THE BOOK" in sent
    assert "unknown" not in sent, "posted_orders still knows whose ask it was"


def test_the_headline_and_recall_both_reach_the_scout(exchange):
    provider = ScriptedProvider(["BEHAVIOURAL: past lots in this category paid off",
                                 "BID: 900 worth a look"])
    broker = Broker("m_buyer", exchange, provider)
    from exchange.agents.context import ContextState
    broker.subconscious.consolidate(ContextState(facts=("x",)), "house", "skincare")

    broker.value_insight("skincare AOV up 12%", "skincare", cap=50_000)

    sent = provider.calls[-1]["messages"][0].content
    assert "skincare AOV up 12%" in sent
    assert "past lots in this category paid off" in sent
