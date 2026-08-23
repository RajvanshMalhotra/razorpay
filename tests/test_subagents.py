from exchange.agents.context import ContextDelta
from exchange.agents.subagents import (
    SubAgent,
    make_diplomat,
    make_scout,
    make_trader,
)
from exchange.agents.tree import ContextTree
from exchange.llm.scripted import ScriptedProvider


def _tree_with_root():
    tree = ContextTree()
    root = tree.add(None, ContextDelta(objective="trade well"), state_version=1)
    return tree, root


def test_act_returns_the_models_text():
    tree, root = _tree_with_root()
    provider = ScriptedProvider(["merchant_41 quotes 1940"])
    agent = make_trader(provider, tree, root, state_version=1)

    assert agent.act("find packaging") == "merchant_41 quotes 1940"


def test_the_role_prompt_is_sent_as_system():
    tree, root = _tree_with_root()
    provider = ScriptedProvider(["ok"])
    agent = make_diplomat(provider, tree, root, state_version=1)

    agent.act("assess merchant_41")

    assert "diplomat" in provider.calls[0]["system"].lower()


def test_the_inherited_context_reaches_the_prompt():
    tree, root = _tree_with_root()
    provider = ScriptedProvider(["ok"])
    agent = make_trader(provider, tree, root, state_version=1)

    agent.act("find packaging")

    assert "trade well" in provider.calls[0]["messages"][0].content


def test_supplied_facts_reach_the_prompt():
    tree, root = _tree_with_root()
    provider = ScriptedProvider(["ok"])
    agent = make_scout(provider, tree, root, state_version=1)

    agent.act("what is rising?", facts=("vitamin C demand up 12%",))

    assert "vitamin C demand up 12%" in provider.calls[0]["messages"][0].content


def test_acting_records_a_node_under_the_agents_own_branch():
    tree, root = _tree_with_root()
    agent = make_trader(ScriptedProvider(["result"]), tree, root, state_version=7)

    agent.act("find packaging")

    assert tree.node(agent.node_id).parent_id is not None
    assert tree.node(agent.node_id).state_version == 7


def test_two_sub_agents_cannot_see_each_others_work():
    """The isolation that stops a broker quoting supplier A while talking to B."""
    tree, root = _tree_with_root()
    trader = make_trader(ScriptedProvider(["we paid 1800 to supplier A"]), tree, root, 1)
    diplomat = make_diplomat(ScriptedProvider(["merchant_41 is reliable"]), tree, root, 1)

    trader.act("negotiate with supplier A")
    diplomat.act("assess merchant_41")

    diplomat_context = tree.materialise(diplomat.node_id)
    assert not any("1800" in fact for fact in diplomat_context.facts)


def test_each_act_appends_to_the_agents_own_chain():
    tree, root = _tree_with_root()
    agent = make_trader(ScriptedProvider(["first", "second"]), tree, root, 1)

    agent.act("step one")
    first = agent.node_id
    agent.act("step two")

    assert agent.node_id != first
    assert tree.node(agent.node_id).parent_id == first


def test_the_result_is_recorded_as_a_fact():
    tree, root = _tree_with_root()
    agent = make_scout(ScriptedProvider(["demand is rising"]), tree, root, 1)

    agent.act("check trends")

    assert "demand is rising" in tree.materialise(agent.node_id).facts


def test_reasoning_effort_is_passed_through():
    tree, root = _tree_with_root()
    provider = ScriptedProvider(["ok"])
    agent = SubAgent("test", "You test.", provider, tree, root, 1, reasoning_effort="low")

    agent.act("do it")

    assert provider.calls[0]["reasoning_effort"] == "low"
