from exchange.agents.context import ContextState
from exchange.agents.subconscious import Subconscious
from exchange.llm.scripted import ScriptedProvider


EPISODE = ContextState(
    objective="buy 500 mailers",
    facts=("merchant_41 opened at 2200", "settled at 1940", "delivery slipped two days"),
)


def test_consolidate_produces_a_lesson_from_the_episode():
    provider = ScriptedProvider(["BEHAVIOURAL: pushes hard on delivery dates"])
    sub = Subconscious(provider)

    lesson = sub.consolidate(EPISODE, "merchant_41", "packaging")

    assert lesson.counterparty_id == "merchant_41"
    assert lesson.category == "packaging"
    assert "delivery" in lesson.text


def test_a_behavioural_lesson_is_marked_behavioural():
    sub = Subconscious(ScriptedProvider(["BEHAVIOURAL: haggles hard then folds"]))

    lesson = sub.consolidate(EPISODE, "merchant_41", "packaging")

    assert lesson.kind == "behavioural"


def test_a_reliability_lesson_is_marked_reliability():
    """Only these should move a counterparty's reliability score."""
    sub = Subconscious(ScriptedProvider(["RELIABILITY: did not deliver on time"]))

    lesson = sub.consolidate(EPISODE, "merchant_41", "packaging")

    assert lesson.kind == "reliability"


def test_an_unlabelled_lesson_defaults_to_behavioural():
    """Behavioural is the safe default — it does not cost anyone their score."""
    sub = Subconscious(ScriptedProvider(["they seem to prefer volume deals"]))

    lesson = sub.consolidate(EPISODE, "merchant_41", "packaging")

    assert lesson.kind == "behavioural"


def test_the_episode_reaches_the_model():
    provider = ScriptedProvider(["BEHAVIOURAL: x"])
    sub = Subconscious(provider)

    sub.consolidate(EPISODE, "merchant_41", "packaging")

    assert "settled at 1940" in provider.calls[0]["messages"][0].content


def test_recall_returns_lessons_for_that_counterparty_only():
    sub = Subconscious(ScriptedProvider([
        "BEHAVIOURAL: 41 pushes on delivery",
        "BEHAVIOURAL: 09 pays early",
    ]))
    sub.consolidate(EPISODE, "merchant_41", "packaging")
    sub.consolidate(EPISODE, "merchant_09", "packaging")

    recalled = sub.recall("merchant_41")

    assert any("41 pushes" in r for r in recalled)
    assert not any("09 pays" in r for r in recalled)


def test_recall_can_narrow_to_a_category():
    sub = Subconscious(ScriptedProvider([
        "BEHAVIOURAL: slow on packaging",
        "BEHAVIOURAL: quick on skincare",
    ]))
    sub.consolidate(EPISODE, "merchant_41", "packaging")
    sub.consolidate(EPISODE, "merchant_41", "skincare")

    recalled = sub.recall("merchant_41", category="skincare")

    assert recalled == ("quick on skincare",)


def test_recall_for_an_unknown_counterparty_is_empty():
    sub = Subconscious(ScriptedProvider([]))

    assert sub.recall("merchant_never_met") == ()
