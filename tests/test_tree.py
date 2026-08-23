import pytest

from exchange.agents.context import ContextDelta
from exchange.agents.tree import ContextTree


def test_a_single_node_materialises_its_own_delta():
    tree = ContextTree()
    node = tree.add(None, ContextDelta(objective="buy mailers"), state_version=1)

    assert tree.materialise(node).objective == "buy mailers"


def test_a_chain_materialises_every_delta_in_order():
    tree = ContextTree()
    a = tree.add(None, ContextDelta(facts_added=("a",)), state_version=1)
    b = tree.add(a, ContextDelta(facts_added=("b",)), state_version=2)
    c = tree.add(b, ContextDelta(facts_added=("c",)), state_version=3)

    assert tree.materialise(c).facts == ("a", "b", "c")


def test_branches_do_not_see_each_others_deltas():
    """Sub-agents branch from a common parent and must stay isolated."""
    tree = ContextTree()
    root = tree.add(None, ContextDelta(facts_added=("shared",)), state_version=1)
    left = tree.add(root, ContextDelta(facts_added=("left only",)), state_version=2)
    right = tree.add(root, ContextDelta(facts_added=("right only",)), state_version=2)

    assert tree.materialise(left).facts == ("shared", "left only")
    assert tree.materialise(right).facts == ("shared", "right only")


def test_a_checkpoint_materialises_the_state_at_that_node():
    tree = ContextTree()
    a = tree.add(None, ContextDelta(facts_added=("a",)), state_version=1)
    b = tree.add(a, ContextDelta(facts_added=("b",)), state_version=2)

    tree.checkpoint(b)

    assert tree.node(b).checkpoint is not None
    assert tree.node(b).checkpoint.facts == ("a", "b")


def test_materialising_past_a_checkpoint_gives_the_same_answer():
    """The checkpoint is an optimisation; it must not change the result."""
    tree = ContextTree()
    a = tree.add(None, ContextDelta(facts_added=("a",)), state_version=1)
    b = tree.add(a, ContextDelta(facts_added=("b",)), state_version=2)
    tree.checkpoint(b)
    c = tree.add(b, ContextDelta(facts_added=("c",)), state_version=3)

    assert tree.materialise(c).facts == ("a", "b", "c")


def test_materialising_stops_walking_at_the_nearest_checkpoint():
    tree = ContextTree()
    node = tree.add(None, ContextDelta(facts_added=("deep",)), state_version=1)
    for i in range(20):
        node = tree.add(node, ContextDelta(facts_added=(f"f{i}",)), state_version=i + 2)
    tree.checkpoint(node)
    tail = tree.add(node, ContextDelta(facts_added=("tail",)), state_version=99)

    assert tree.walk_length(tail) == 2  # the tail node plus the checkpointed node


def test_ancestors_are_returned_nearest_first():
    tree = ContextTree()
    a = tree.add(None, ContextDelta(objective="root"), state_version=1)
    b = tree.add(a, ContextDelta(facts_added=("b",)), state_version=2)
    c = tree.add(b, ContextDelta(facts_added=("c",)), state_version=3)

    assert [n.node_id for n in tree.ancestors(c)] == [b, a]


def test_a_node_records_the_state_version_it_saw():
    tree = ContextTree()
    node = tree.add(None, ContextDelta(), state_version=8412)

    assert tree.node(node).state_version == 8412


def test_an_unknown_node_raises():
    tree = ContextTree()

    with pytest.raises(KeyError):
        tree.materialise("nope")
