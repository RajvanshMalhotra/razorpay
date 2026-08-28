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


def auction(events):
    """The one insight lot, its bids, and where the royalties went.

    Returned as raw events rather than a summary: the persuasive thing about
    an auction is the numbers, and a summary is a place for them to drift.
    """
    minted = next((e for e in events if e.type == "INSIGHT_MINTED"), None)
    if minted is None:
        return None
    corr = minted.correlation_id
    thread = [e for e in events if e.correlation_id == corr]
    bids = [e for e in thread if e.type == "BID_PLACED"]
    cleared = next((e for e in thread if e.type == "AUCTION_CLEARED"), None)
    royalties = [e for e in thread if e.type == "CREDITS_TRANSFERRED"
                 and e.payload.get("from_actor_id") == "house"]
    return {
        "headline": (minted.payload.get("spec") or minted.payload).get("headline"),
        "contributors": len((minted.payload.get("spec") or minted.payload)
                            .get("contributor_ids") or ()),
        "bids": sorted(bids, key=lambda e: e.payload.get("amount", 0), reverse=True),
        "winner": cleared.payload.get("winner_id") if cleared else None,
        "price": cleared.payload.get("price") if cleared else None,
        "royalties": royalties,
        "correlation_id": corr,
    }


def lessons(events, limit: int = 6):
    """What merchants learned, in their own words."""
    return [e for e in events if e.type == "LESSON_CONSOLIDATED"][:limit]


def failure_threads(events) -> list[str]:
    """Correlations where the accountant caught and repaired a drift.

    The graded requirement, and the replay must be able to find it without
    being told which trade it was — the same way the accountant did.
    """
    drifted = {e.correlation_id for e in events if e.type == "DRIFT_DETECTED"}
    repaired = {e.correlation_id for e in events
                if e.type == "SETTLEMENT_COMPLETED" and e.actor_id == "accountant"}
    return sorted(drifted & repaired)


# Plain language for every event type. A judge should not have to learn our
# vocabulary to follow the tape — the raw type stays visible beside it,
# because the raw type is what makes the log checkable.
SAYS = {
    "ACTOR_REGISTERED": "joined the exchange",
    "ASSET_LISTED": "listed something for sale",
    "ORDER_POSTED": "posted what it needs",
    "COUNTERPARTY_CHOSEN": "picked who to deal with",
    "NEGOTIATION_OPENED": "opened talks",
    "NEGOTIATION_ROUND": "made an offer",
    "NEGOTIATION_ENDED": "talks ended",
    "MATCH_PROPOSED": "proposed the terms",
    "POLICY_DECIDED": "the gate ruled",
    "SETTLEMENT_INITIATED": "money committed",
    "SETTLEMENT_COMPLETED": "payment confirmed",
    "SETTLEMENT_FAILED": "settlement failed",
    "TURN_ENDED": "turn ended",
    "POINTS_MINTED": "earned points",
    "LESSON_CONSOLIDATED": "learned something",
    "DRIFT_DETECTED": "books disagree with Razorpay",
    "ACTOR_FROZEN": "trading stopped",
    "ACTOR_RESUMED": "trading resumed",
    "RECONCILED": "books checked",
    "INSIGHT_MINTED": "market intelligence minted",
    "AUCTION_OPENED": "auction opened",
    "BID_PLACED": "bid placed",
    "AUCTION_CLEARED": "auction cleared",
    "CREDITS_TRANSFERRED": "points moved",
    "PRIVACY_REFUSED": "privacy floor refused",
    "PAYMENT_LINK_REISSUED": "payment link reissued",
    "ORDER_FILLED": "order filled",
    "RECONCILE_CHECK_FAILED": "could not check this one",
}


def tape(events, limit: int = 420):
    """The market as a stream a viewer can watch play.

    Every row is a real event in the order it happened. Trimmed to the types
    that carry the story — a viewer watching 961 rows learns less than one
    watching 400, and the full log is a click away in each panel.
    """
    keep = {
        "ORDER_POSTED", "COUNTERPARTY_CHOSEN", "NEGOTIATION_ROUND",
        "NEGOTIATION_ENDED", "POLICY_DECIDED", "SETTLEMENT_INITIATED",
        "SETTLEMENT_COMPLETED", "POINTS_MINTED", "LESSON_CONSOLIDATED",
        "DRIFT_DETECTED", "ACTOR_FROZEN", "ACTOR_RESUMED", "INSIGHT_MINTED",
        "BID_PLACED", "AUCTION_CLEARED", "PRIVACY_REFUSED",
    }
    out = []
    for e in events:
        if e.type not in keep:
            continue
        p = e.payload
        detail, tone = "", ""
        if e.type == "POLICY_DECIDED":
            verdict = p.get("verdict", "")
            tone = "allow" if verdict == "ALLOW" else "deny"
            detail = f"{verdict} — {p.get('reason','')}"
        elif e.type == "NEGOTIATION_ROUND":
            detail = f"{p.get('price')} — {str(p.get('message','')).strip()}"
        elif e.type == "NEGOTIATION_ENDED":
            ok = p.get("agreed")
            tone = "allow" if ok else "deny"
            detail = (f"agreed at {p.get('final_price')}" if ok
                      else str(p.get("reason", "")))
        elif e.type == "SETTLEMENT_INITIATED":
            detail = f"{p.get('amount',0)/100:,.2f} rupees · {p.get('razorpay_order_id','')}"
        elif e.type == "SETTLEMENT_COMPLETED":
            tone = "allow"
            detail = str(p.get("razorpay_payment_id", ""))
        elif e.type == "POINTS_MINTED":
            tone = "amber"
            detail = f"{p.get('points')} points"
        elif e.type == "LESSON_CONSOLIDATED":
            detail = str(p.get("text", ""))[:120]
        elif e.type == "DRIFT_DETECTED":
            tone = "deny"
            detail = (f"local {p.get('local_status')} vs "
                      f"remote {p.get('remote_status')}")
        elif e.type in ("ACTOR_FROZEN",):
            tone = "deny"
            detail = str(p.get("reason", ""))
        elif e.type == "ACTOR_RESUMED":
            tone = "allow"
            detail = "cleared to trade again"
        elif e.type == "INSIGHT_MINTED":
            tone = "amber"
            detail = str((p.get("spec") or p).get("headline", ""))[:130]
        elif e.type == "BID_PLACED":
            detail = f"{p.get('amount')} points"
        elif e.type == "AUCTION_CLEARED":
            tone = "amber"
            detail = f"{p.get('winner_id')} pays {p.get('price')}"
        elif e.type == "ORDER_POSTED":
            q = p.get("asset_query") or {}
            detail = q.get("text", "") or f"selling {p.get('qty')} units"
        elif e.type == "PRIVACY_REFUSED":
            tone = "deny"
            detail = str(p.get("reason", ""))
        out.append({
            "seq": e.seq, "actor": e.actor_id, "type": e.type,
            "says": SAYS.get(e.type, e.type.lower().replace("_", " ")),
            "detail": detail[:150], "tone": tone,
        })
    step = max(1, len(out) // limit)
    return out[::step][:limit]


def storefront(events):
    """The recorded human purchase — the one trade a person drove.

    Kept whole rather than summarised: the claim it supports is that a person
    reaches the same machinery, and the evidence for that is the identical
    event sequence on its own correlation id.
    """
    threads = {}
    for e in events:
        if e.correlation_id.startswith("shop_"):
            threads.setdefault(e.correlation_id, []).append(e)
    # the one that actually settled, else the longest attempt
    settled = [t for t in threads.values()
               if any(x.type == "SETTLEMENT_INITIATED" for x in t)]
    chosen = (settled or sorted(threads.values(), key=len, reverse=True) or [[]])[0]
    query = next((e.payload.get("asset_query", {}).get("text", "")
                  for e in chosen if e.type == "ORDER_POSTED"), "")
    return {
        "query": query,
        "rows": [{"actor": e.actor_id, "type": e.type,
                  "says": SAYS.get(e.type, e.type), "seq": e.seq}
                 for e in chosen],
    }


def catalogue(events, limit: int = 40):
    """What is actually for sale, so the storefront box can answer honestly."""
    out = {}
    for e in events:
        if e.type == "ASSET_LISTED":
            p = e.payload
            out[p.get("asset_id")] = {"title": p.get("title", ""),
                                      "seller": e.actor_id}
    for e in events:
        if e.type == "ORDER_POSTED" and e.payload.get("asset_ref") in out:
            out[e.payload["asset_ref"]]["price"] = e.payload.get("limit_price")
            out[e.payload["asset_ref"]]["qty"] = e.payload.get("qty")
    return [v | {"id": k} for k, v in out.items() if v.get("price")][:limit]
