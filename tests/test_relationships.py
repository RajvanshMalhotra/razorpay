from exchange.agents.relationships import UNKNOWN_STANDING, RelationshipGraph
from exchange.agents.subconscious import Lesson


def test_an_unknown_counterparty_is_scored_optimistically():
    """Above neutral on purpose: the only way to learn is to deal with them."""
    graph = RelationshipGraph()

    assert graph.standing("never_met") == UNKNOWN_STANDING
    assert UNKNOWN_STANDING > 0.5


def test_an_unknown_counterparty_has_no_confidence():
    graph = RelationshipGraph()

    assert graph.confidence("never_met") == 0.0


def test_confidence_rises_with_deals():
    graph = RelationshipGraph()
    before = graph.confidence("m_41")

    for _ in range(3):
        graph.record_deal("m_41", value=100_000, delivered=True)

    assert graph.confidence("m_41") > before


def test_a_delivered_deal_raises_standing():
    graph = RelationshipGraph()
    graph.record_deal("m_41", value=100_000, delivered=True)

    assert graph.standing("m_41") > 0.5


def test_a_failed_delivery_lowers_standing():
    graph = RelationshipGraph()
    graph.record_deal("m_41", value=100_000, delivered=False)

    assert graph.standing("m_41") < UNKNOWN_STANDING


def test_one_bad_deal_does_not_collapse_a_long_record():
    graph = RelationshipGraph()
    for _ in range(10):
        graph.record_deal("m_41", value=100_000, delivered=True)
    strong = graph.standing("m_41")

    graph.record_deal("m_41", value=100_000, delivered=False)

    assert graph.standing("m_41") > strong - 0.2


def test_a_behavioural_lesson_does_not_move_standing():
    """Haggling hard is business, not unreliability."""
    graph = RelationshipGraph()
    graph.record_deal("m_41", value=100_000, delivered=True)
    before = graph.standing("m_41")

    graph.apply_lesson(Lesson("m_41", "packaging", "haggles hard", "behavioural"))

    assert graph.standing("m_41") == before


def test_a_reliability_lesson_moves_standing():
    graph = RelationshipGraph()
    graph.record_deal("m_41", value=100_000, delivered=True)
    before = graph.standing("m_41")

    graph.apply_lesson(Lesson("m_41", "packaging", "did not deliver", "reliability"))

    assert graph.standing("m_41") < before


def test_a_reliability_lesson_bites_even_with_no_recorded_deal():
    """standing used to return UNKNOWN_STANDING whenever deals == 0, so the
    penalty was silently discarded and a counterparty who took a deal and never
    delivered kept the optimistic score forever."""
    graph = RelationshipGraph()

    graph.apply_lesson(Lesson("m_41", "packaging", "took the money, no goods",
                              "reliability"))

    assert graph.standing("m_41") < UNKNOWN_STANDING


def test_a_behavioural_lesson_alone_leaves_a_stranger_optimistic():
    """The other half: behavioural lessons are advice, not evidence of harm."""
    graph = RelationshipGraph()

    graph.apply_lesson(Lesson("m_41", "packaging", "haggles hard", "behavioural"))

    assert graph.standing("m_41") == UNKNOWN_STANDING


def test_scores_returns_every_known_counterparty():
    graph = RelationshipGraph()
    graph.record_deal("m_41", value=1, delivered=True)
    graph.record_deal("m_09", value=1, delivered=True)

    assert set(graph.scores()) == {"m_41", "m_09"}


def test_standing_stays_within_zero_and_one():
    graph = RelationshipGraph()
    for _ in range(50):
        graph.record_deal("m_bad", value=1, delivered=False)
    for _ in range(50):
        graph.record_deal("m_good", value=1, delivered=True)

    assert 0.0 <= graph.standing("m_bad") <= 1.0
    assert 0.0 <= graph.standing("m_good") <= 1.0
