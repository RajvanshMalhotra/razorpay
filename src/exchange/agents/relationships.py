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

KNOWN GAP, STATED PLAINLY BECAUSE IT IS A POLICY INPUT. `_records` is process
memory. It is NOT a projection of the event log, nothing folds it, and nothing
persists it. `confidence()` therefore has two consequences worth naming rather
than discovering:

  - The accountant cannot verify it. `counterparty_confidence` is the input
    that decides whether the unknown-counterparty cap binds, and it is the one
    figure reaching the gate that is not reconstructible from the log — so the
    audit trail records what the gate was told, not that what it was told was
    true. Everywhere else in this system the authoritative figure is derived
    from the log precisely so the party it constrains cannot supply it; this
    is the exception, and it is an exception by omission, not by design.
  - It does not ratchet across runs. Every process starts with an empty graph,
    so confidence is 0.0 on the hundredth deal as on the first and the trial
    cap binds identically forever. "Cap rises with track record" has no path
    to firing until this is folded from the log.

Deferred deliberately: folding it belongs with the work on a populated market,
where a track record exists to ratchet. Until then, no comment here or at the
call site may describe this number as log-derived.
"""
from __future__ import annotations

from dataclasses import dataclass

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
