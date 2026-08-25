"""Agent reasoning, written into the same log as the money.

A trade's `correlation_id` should reconstruct not just what was bought but
what the agent weighed on the way. These events share that id with the
orders, decisions and settlements, so a replay follows one story rather than
stitching two streams together.
"""
from __future__ import annotations

from exchange import events as ev
from exchange.eventlog import EventLog


class AgentJournal:
    def __init__(self, log: EventLog, actor_id: str, correlation_id: str) -> None:
        self._log = log
        self._actor_id = actor_id
        self._correlation_id = correlation_id

    def _append(self, type: str, payload: dict, actor_id: str | None = None) -> None:
        self._log.append(
            actor_id or self._actor_id, type, payload,
            correlation_id=self._correlation_id,
        )

    def recall_injected(self, counterparty_id: str, lessons: tuple[str, ...]) -> None:
        self._append(ev.RECALL_INJECTED, {
            "counterparty_id": counterparty_id,
            "lessons": list(lessons),
        })

    def lesson_consolidated(self, lesson) -> None:
        self._append(ev.LESSON_CONSOLIDATED, {
            "counterparty_id": lesson.counterparty_id,
            "category": lesson.category,
            "text": lesson.text,
            "kind": lesson.kind,
        })

    def negotiation_opened(self, counterparty_id: str, opening_price: int) -> None:
        self._append(ev.NEGOTIATION_OPENED, {
            "counterparty_id": counterparty_id,
            "opening_price": opening_price,
        })

    def negotiation_round(self, actor_id: str, price: int, message: str) -> None:
        """Attributed to WHOEVER SPOKE, not to the journal's owner.

        The journal belongs to the merchant that opened the negotiation, so
        every round was landing in the log under that merchant's name — the
        payload named the real speaker but the envelope did not. Reading the
        log by actor showed one merchant apparently arguing with itself, and
        a replay that groups by envelope would have shown the same.
        """
        self._append(ev.NEGOTIATION_ROUND, {
            "by": actor_id,
            "price": price,
            "message": message,
        }, actor_id=actor_id)

    def negotiation_ended(self, agreed: bool, final_price: int | None, reason: str) -> None:
        self._append(ev.NEGOTIATION_ENDED, {
            "agreed": agreed,
            "final_price": final_price,
            "reason": reason,
        })
