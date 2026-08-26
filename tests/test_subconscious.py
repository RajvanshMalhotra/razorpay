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


# --- memory across a boundary no earlier test crossed -------------------------
#
# Every test above consolidates and recalls inside ONE process, and the bug
# only existed between two. A market that ran for two hours across three
# resumptions woke up amnesiac twice, and nothing failed.


def test_lessons_survive_a_new_process(tmp_path):
    """The test that was missing.

    Lessons were always WRITTEN to the log; nothing ever read them back.
    """
    from exchange.agents.journal import AgentJournal
    from exchange.eventlog import EventLog

    db = str(tmp_path / "mem.db")

    log = EventLog(db)
    first = Subconscious(ScriptedProvider(["BEHAVIOURAL: they haggle but always fold on delivery"]))
    lesson = first.consolidate(EPISODE, "m_kaapi", category="trade")
    AgentJournal(log, "m_buyer", "c1").lesson_consolidated(lesson)
    log.close()

    log = EventLog(db)                       # a different process, same log
    resumed = Subconscious(ScriptedProvider(["unused"]), log=log)

    assert resumed.recall("m_kaapi") == ("they haggle but always fold on delivery",)
    log.close()


def test_a_restored_lesson_keeps_its_kind(tmp_path):
    """The reliability/behavioural split is load-bearing: 'they haggle hard'
    must never lower a trust score, 'they did not deliver' must. A restore
    that flattened the two would quietly punish good negotiators."""
    from exchange.agents.journal import AgentJournal
    from exchange.eventlog import EventLog

    db = str(tmp_path / "mem.db")
    log = EventLog(db)
    sub = Subconscious(ScriptedProvider(["RELIABILITY: delivered late twice"]))
    AgentJournal(log, "m_buyer", "c1").lesson_consolidated(
        sub.consolidate(EPISODE, "m_slow", category="trade")
    )
    log.close()

    log = EventLog(db)
    restored = Subconscious(ScriptedProvider(["unused"]), log=log).lessons
    assert [l.kind for l in restored] == ["reliability"]
    log.close()


def test_recall_still_filters_after_a_restore(tmp_path):
    """The read gate has to survive the round trip, or a restored merchant
    leaks one counterparty's history into another's negotiation."""
    from exchange.agents.journal import AgentJournal
    from exchange.eventlog import EventLog

    db = str(tmp_path / "mem.db")
    log = EventLog(db)
    journal = AgentJournal(log, "m_buyer", "c1")
    sub = Subconscious(ScriptedProvider(["BEHAVIOURAL: pushes hard on dates"] * 4))
    journal.lesson_consolidated(sub.consolidate(EPISODE, "m_a", category="trade"))
    journal.lesson_consolidated(sub.consolidate(EPISODE, "m_b", category="trade"))
    log.close()

    log = EventLog(db)
    resumed = Subconscious(ScriptedProvider(["unused"]), log=log)

    assert len(resumed.recall("m_a")) == 1
    assert len(resumed.recall("m_b")) == 1
    assert resumed.recall("m_never_met") == ()
    log.close()


def test_a_subconscious_with_no_log_starts_empty(tmp_path):
    """Right for a test, wrong for a market — and it must stay possible."""
    assert Subconscious(ScriptedProvider(["x"])).lessons == ()
