"""The part that only remembers.

It never acts. After an episode it distils what happened into a durable
lesson; before a sub-agent acts it injects the lessons that matter. That is
what makes a broker feel like it has history rather than a fresh mind each
time.

Lessons are split by kind because the two carry different weight.
"They haggle hard" is behavioural — useful, and costs the counterparty
nothing. "They did not deliver" is reliability — and only that kind moves a
counterparty's score. An unlabelled lesson defaults to behavioural, because
the safe failure is to under-punish rather than to mark someone unreliable on
a model's stray word.
"""
from __future__ import annotations

from dataclasses import dataclass

from exchange.agents.context import ContextState, render
from exchange import events as ev
from exchange.llm.base import LLMMessage, LLMProvider

CONSOLIDATE_PROMPT = """You review a completed business deal and write down the single
most useful thing to remember about the counterparty for next time.

Begin your answer with exactly one of:
  BEHAVIOURAL:   how they negotiate — haggling, pace, what they respond to
  RELIABILITY:   whether they did what they promised — delivery, payment, quality

Then one sentence. Be specific and concrete. Write nothing else."""


@dataclass(frozen=True)
class Lesson:
    counterparty_id: str
    category: str
    text: str
    kind: str  # "behavioural" | "reliability"


class Subconscious:
    def __init__(self, provider: LLMProvider, log=None) -> None:
        """Lessons are RESTORED FROM THE LOG, not started empty.

        They were always written to it — `LESSON_CONSOLIDATED` carries the
        counterparty, the category, the text and the kind, which is
        everything a lesson is — and nothing ever read them back. So the
        list below began empty on every process, and a market that ran for
        two hours across three resumptions woke up amnesiac twice: every
        merchant had forgotten who had haggled, who had delivered, and who
        it had never met.

        Nothing failed and no test caught it, because every test
        consolidates and recalls inside one process. The bug only exists
        across a boundary no test crosses.

        This is the same defect as `counterparty_confidence` being process
        memory, and that one was known — found, written up, and deferred.
        What was never asked is what ELSE lived only in memory. The answer
        was the differentiator.
        """
        self._provider = provider
        self._lessons: list[Lesson] = []
        if log is not None:
            self._lessons.extend(_lessons_from(log))

    @property
    def lessons(self) -> tuple[Lesson, ...]:
        return tuple(self._lessons)

    def consolidate(
        self,
        episode: ContextState,
        counterparty_id: str,
        category: str,
    ) -> Lesson:
        response = self._provider.complete(
            [LLMMessage("user", render(episode))],
            system=CONSOLIDATE_PROMPT,
            max_tokens=900,
            reasoning_effort="low",
        )
        text = response.text.strip()

        upper = text.upper()
        if upper.startswith("RELIABILITY:"):
            kind, text = "reliability", text[len("RELIABILITY:"):].strip()
        elif upper.startswith("BEHAVIOURAL:"):
            kind, text = "behavioural", text[len("BEHAVIOURAL:"):].strip()
        else:
            kind = "behavioural"

        lesson = Lesson(counterparty_id, category, text, kind)
        self._lessons.append(lesson)
        return lesson

    def recall(
        self,
        counterparty_id: str,
        category: str | None = None,
    ) -> tuple[str, ...]:
        return tuple(
            lesson.text
            for lesson in self._lessons
            if lesson.counterparty_id == counterparty_id
            and (category is None or lesson.category == category)
        )


def _lessons_from(log) -> list[Lesson]:
    """Every lesson this merchant has ever consolidated, in the order learned.

    Order matters: `recall` returns lessons as it holds them, so the oldest
    reading of a counterparty arrives first and the most recent last — which
    is how a reader would want them, and how they were learned.
    """
    return [
        Lesson(
            counterparty_id=e.payload["counterparty_id"],
            category=e.payload["category"],
            text=e.payload["text"],
            kind=e.payload.get("kind", "behavioural"),
        )
        for e in log.read_all()
        if e.type == ev.LESSON_CONSOLIDATED
    ]
