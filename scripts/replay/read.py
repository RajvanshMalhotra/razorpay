"""Read a market log into the shapes a replay needs.

READS ONLY. This module and everything under `scripts/replay/` never write to
the log, never call a model, and never touch Razorpay. The replay's whole
claim is that it shows what happened rather than making anything happen, and
a reader that could write would quietly make that claim unverifiable.

Every figure comes from the log or from `fold`, the same projection the
exchange itself runs on. Nothing is recomputed by hand: a total on screen that
the projection disagrees with is exactly the drift the accountant exists to
catch, and it would be embarrassing for the replay to invent one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from exchange.eventlog import EventLog
from exchange.projections import fold


@dataclass
class Trade:
    """One merchant's turn, as a reader would follow it."""
    correlation_id: str
    buyer_id: str = ""
    need: str = ""
    seller_id: str = ""
    events: list = field(default_factory=list)
    agreed_price: int | None = None
    settled_amount: int | None = None
    outcome: str = ""
    payment_link: str | None = None

    @property
    def gate_decisions(self) -> list:
        return [e for e in self.events if e.type == "POLICY_DECIDED"]

    @property
    def was_refused_then_allowed(self) -> bool:
        """The trial trade: refused at full size, allowed smaller.

        The most valuable single thing in the log — the anti-incumbency cap
        visible in one trade — so the replay needs to be able to find it.
        """
        verdicts = [d.payload.get("verdict") for d in self.gate_decisions]
        return "DENY" in verdicts and "ALLOW" in verdicts


@dataclass
class MarketSummary:
    events: int = 0
    merchants: int = 0
    orders: int = 0
    negotiations: int = 0
    agreed: int = 0
    walked: int = 0
    settlements: int = 0
    completed: int = 0
    distinct_traders: int = 0
    value_paise: int = 0
    gate_allow: int = 0
    gate_deny: int = 0
    points_minted: int = 0
    lessons: int = 0
    insights: int = 0


def load(db_path: str):
    """(summary, trades, events) — everything a page needs, read once."""
    log = EventLog(db_path)
    try:
        events = log.read_all()
    finally:
        log.close()

    state = fold(events)
    summary = MarketSummary(
        events=len(events),
        merchants=len(state.actors),
        orders=sum(1 for e in events if e.type == "ORDER_POSTED"),
        negotiations=sum(1 for e in events if e.type == "NEGOTIATION_ENDED"),
        agreed=sum(1 for e in events
                   if e.type == "NEGOTIATION_ENDED" and e.payload.get("agreed")),
        walked=sum(1 for e in events
                   if e.type == "NEGOTIATION_ENDED" and not e.payload.get("agreed")),
        settlements=sum(1 for e in events if e.type == "SETTLEMENT_INITIATED"),
        completed=sum(1 for e in events if e.type == "SETTLEMENT_COMPLETED"),
        distinct_traders=len({e.actor_id for e in events
                              if e.type == "SETTLEMENT_INITIATED"}),
        value_paise=sum(e.payload.get("amount", 0) for e in events
                        if e.type == "SETTLEMENT_INITIATED"),
        gate_allow=sum(1 for e in events if e.type == "POLICY_DECIDED"
                       and e.payload.get("verdict") == "ALLOW"),
        gate_deny=sum(1 for e in events if e.type == "POLICY_DECIDED"
                      and e.payload.get("verdict") != "ALLOW"),
        points_minted=sum(1 for e in events if e.type == "POINTS_MINTED"),
        lessons=sum(1 for e in events if e.type == "LESSON_CONSOLIDATED"),
        insights=sum(1 for e in events if e.type == "INSIGHT_MINTED"),
    )

    trades: dict[str, Trade] = {}
    for event in events:
        if not event.correlation_id.startswith("turn_"):
            continue
        trade = trades.setdefault(event.correlation_id,
                                  Trade(correlation_id=event.correlation_id))
        trade.events.append(event)

        if event.type == "ORDER_POSTED" and not trade.buyer_id:
            trade.buyer_id = event.actor_id
            query = event.payload.get("asset_query") or {}
            trade.need = query.get("text", "")
        elif event.type == "NEGOTIATION_OPENED":
            trade.seller_id = event.payload.get("counterparty_id", "")
        elif event.type == "NEGOTIATION_ENDED" and event.payload.get("agreed"):
            trade.agreed_price = event.payload.get("final_price")
        elif event.type == "SETTLEMENT_INITIATED":
            trade.settled_amount = event.payload.get("amount")
            trade.payment_link = event.payload.get("payment_link_url")
        elif event.type == "TURN_ENDED":
            trade.outcome = event.payload.get("outcome", "")

    return summary, list(trades.values()), events


def failure_threads(events) -> list[str]:
    """Correlations where the accountant caught and repaired a drift.

    The graded requirement, and the replay must be able to find it without
    being told which trade it was — the same way the accountant did.
    """
    drifted = {e.correlation_id for e in events if e.type == "DRIFT_DETECTED"}
    repaired = {e.correlation_id for e in events
                if e.type == "SETTLEMENT_COMPLETED" and e.actor_id == "accountant"}
    return sorted(drifted & repaired)
