"""Who we have dealt with, and how it went.

Two numbers per counterparty, and they answer different questions.

  standing    how well they have behaved. Feeds the matcher as a soft nudge.
  confidence  how much we actually know. Feeds the policy gate, which caps
              trade size with strangers.

An unknown counterparty is scored ABOVE neutral, not below. The market would
otherwise ossify into cliques: known merchants win all the business, accrue
more history, get preferred harder, and a new merchant never gets a first
deal. Optimism plus a small cap means we try strangers and risk little doing
it.

Evidence is weighted: one bad deal against a long good record barely moves
the number, because one data point is weak evidence, not a verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field

UNKNOWN_STANDING = 0.65
CONFIDENCE_FULL_AT = 5  # deals needed before we consider ourselves informed
RELIABILITY_LESSON_PENALTY = 2  # counts as this many failed deals


@dataclass
class _Record:
    delivered: int = 0
    failed: int = 0
    deals: int = 0
    total_value: int = 0


class RelationshipGraph:
    def __init__(self) -> None:
        self._records: dict[str, _Record] = {}

    def _record(self, counterparty_id: str) -> _Record:
        return self._records.setdefault(counterparty_id, _Record())

    def record_deal(self, counterparty_id: str, value: int, delivered: bool) -> None:
        record = self._record(counterparty_id)
        record.deals += 1
        record.total_value += value
        if delivered:
            record.delivered += 1
        else:
            record.failed += 1

    def apply_lesson(self, lesson) -> None:
        """Only reliability lessons move standing. Behavioural ones are advice."""
        if lesson.kind != "reliability":
            return
        record = self._record(lesson.counterparty_id)
        record.failed += RELIABILITY_LESSON_PENALTY

    def standing(self, counterparty_id: str) -> float:
        record = self._records.get(counterparty_id)
        # Falls through on ANY evidence, not just on a recorded deal. Gating on
        # deals == 0 discarded a reliability lesson's penalty whenever no deal
        # had been recorded — so a counterparty who took a deal and never
        # delivered kept the optimistic UNKNOWN_STANDING forever, which is the
        # one case the penalty exists for.
        if record is None or (record.deals == 0 and record.failed == 0
                              and record.delivered == 0):
            return UNKNOWN_STANDING
        # Laplace-smoothed success rate: pulls toward neutral when evidence is thin,
        # so a single outcome cannot swing the score to an extreme.
        score = (record.delivered + 1) / (record.delivered + record.failed + 2)
        return max(0.0, min(1.0, score))

    def confidence(self, counterparty_id: str) -> float:
        record = self._records.get(counterparty_id)
        if record is None:
            return 0.0
        return min(1.0, record.deals / CONFIDENCE_FULL_AT)

    def scores(self) -> dict[str, float]:
        return {cid: self.standing(cid) for cid in self._records}
