"""Consolidate a lesson for each trade that actually completed.

Consolidation lives in `Broker.close`, which runs when the rail returns
COMPLETED — inline, at the moment of the trade. Every settlement in this
market completes LATER, when someone pays its link and the accountant
repairs the drift, so the broker that made the trade is long gone and no
lesson was ever filed. The differentiator ran zero times on real data.

Same shape as points before it: a consequence of a settlement that only
fired on one of the two paths a settlement can take.

The lesson is built from THE LOG, not from a live broker's memory: what was
asked, what was agreed, how many rounds it took, and whether the payment
actually arrived. That is what the Subconscious would have seen, and it is
checkable afterwards by anyone reading the same events.
"""
from __future__ import annotations

from exchange import events as ev
from exchange.agents.context import ContextState
from exchange.agents.journal import AgentJournal


def episodes_from(log):
    """One episode per completed trade, read out of the log."""
    events = log.read_all()
    completed = {
        e.payload["settlement_id"]
        for e in events if e.type == ev.SETTLEMENT_COMPLETED
    }
    initiated = {
        e.payload["settlement_id"]: e
        for e in events if e.type == ev.SETTLEMENT_INITIATED
    }
    learned = {
        (e.actor_id, e.payload.get("counterparty_id"))
        for e in events if e.type == ev.LESSON_CONSOLIDATED
    }

    by_corr: dict[str, list] = {}
    for event in events:
        by_corr.setdefault(event.correlation_id, []).append(event)

    out = []
    for settlement_id in completed:
        opened = initiated.get(settlement_id)
        if opened is None:
            continue
        thread = by_corr.get(opened.correlation_id, [])
        rounds = [e for e in thread if e.type == ev.NEGOTIATION_ROUND]
        seller = next(
            (e.payload.get("counterparty_id") for e in thread
             if e.type == ev.NEGOTIATION_OPENED), None)
        if not seller or (opened.actor_id, seller) in learned:
            continue
        ended = next((e for e in thread if e.type == ev.NEGOTIATION_ENDED), None)
        facts = [
            f"traded with {seller} for {opened.payload.get('amount')} paise",
            f"the negotiation took {len(rounds)} offers",
        ]
        if ended and ended.payload.get("final_price"):
            facts.append(f"agreed at {ended.payload['final_price']} per unit")
        facts.append("the payment arrived and the settlement completed")
        out.append((opened.actor_id, seller, opened.correlation_id,
                    ContextState(objective=f"trade well for {opened.actor_id}",
                                 facts=tuple(facts))))
    return out


def learn(log, subconscious_for, limit: int = 20) -> int:
    """Consolidate and journal one lesson per completed trade. Returns count."""
    filed = 0
    for actor_id, seller, correlation_id, episode in episodes_from(log)[:limit]:
        try:
            sub = subconscious_for(actor_id)
            lesson = sub.consolidate(episode, seller, category="trade")
            AgentJournal(log, actor_id, correlation_id).lesson_consolidated(lesson)
            filed += 1
        except Exception:  # noqa: BLE001 - one lost lesson is not the batch
            continue
    return filed
