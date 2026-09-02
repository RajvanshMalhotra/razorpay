"""One merchant's sheet: what its agent did, and what that was worth.

NOT AN ANALYST'S VIEW. An earlier version filled the workbook with
market-wide tables — every offer in the market, every ruling, every
counterparty choice. All true, all interesting to whoever runs the exchange,
and none of it a merchant's business. A merchant wants four things: what did
this cost me, what did it save me, who am I dealing with, and what did the
system stop.

WHAT IT SAVED YOU IS THE NUMBER THAT MATTERS. Everything else a merchant can
get from its bank statement. Only the agent knows that a supplier listed at
₹210 and settled at ₹195, and the difference times the quantity is the whole
argument for having an agent at all. It is computed from the seller's own
listed price and the price the negotiation ended at, both read from the log.

A sheet is a title, a strip of headline figures, and a few blocks. The
renderer knows how to draw any of those without knowing what is in them, so a
new section is a function returning rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from exchange import plain
from exchange.books import entries_for


@dataclass
class Block:
    """One table inside a sheet."""
    heading: str
    note: str
    headings: tuple
    rows: list
    widths: tuple
    money: tuple = ()
    counts: tuple = ()
    ident: tuple = ()
    wrap: tuple = ()
    rules: tuple = ()
    empty: str = "Nothing here yet."


@dataclass
class Sheet:
    key: str
    title: str
    subtitle: str
    cards: list = field(default_factory=list)     # (label, value, kind)
    blocks: list = field(default_factory=list)


def _rupees(paise) -> float:
    return round((paise or 0) / 100, 2)


def _who(name) -> str:
    n = str(name or "")
    return n[2:].replace("_", " ") if n.startswith("m_") else n


def _namer(names):
    """A function from actor id to the name a person would say out loud."""
    lookup = dict(names or {})
    return lambda actor: lookup.get(str(actor or "")) or _who(actor).title()


def _spoken(message) -> str:
    """What the agent said, without the price it already said in numbers."""
    import re

    text = " ".join(str(message or "").split())
    text = re.sub(r"^PRICE:\s*[\d,.]+\s*[-—:]?\s*", "", text, flags=re.I)
    return text.strip()


def _deals(events, actor_id):
    """Every trade this merchant was in, with what it was listed at.

    The listed price comes from the seller's own ASK order, which is the only
    honest baseline for a saving: it is what the merchant would have paid if
    its agent had simply accepted the asking price.
    """
    asks = {e.payload.get("order_id"): e.payload.get("limit_price")
            for e in events
            if e.type == "ORDER_POSTED" and e.payload.get("side") == "ASK"}
    seller_of = {e.payload.get("order_id"): e.actor_id
                 for e in events if e.type == "ORDER_POSTED"}

    threads = {}
    for e in events:
        if e.correlation_id.startswith(("turn_", "shop_")):
            threads.setdefault(e.correlation_id, []).append(e)

    out = []
    for corr, thread in threads.items():
        opened = next((e for e in thread
                       if e.type == "SETTLEMENT_INITIATED"), None)
        if opened is None or opened.payload.get("currency") != "INR":
            continue
        matched = next((e for e in thread if e.type == "MATCH_PROPOSED"), None)
        chosen = next((e for e in thread
                       if e.type == "COUNTERPARTY_CHOSEN"), None)
        ask_id = ((chosen.payload.get("ask_order_id") if chosen else None)
                  or (matched.payload.get("ask_order_id") if matched else None))
        buyer = opened.actor_id
        seller = seller_of.get(ask_id, "")
        if actor_id not in (buyer, seller):
            continue

        ended = next((e for e in thread if e.type == "NEGOTIATION_ENDED"
                      and e.payload.get("agreed")), None)
        done = next((e for e in thread
                     if e.type == "SETTLEMENT_COMPLETED"), None)
        posted = next((e for e in thread if e.type == "ORDER_POSTED"), None)
        query = (posted.payload.get("asset_query") or {}) if posted else {}

        paid = (ended.payload.get("final_price") if ended
                else (matched.payload.get("clearing_price")
                      if matched else None))
        amount = opened.payload.get("amount") or 0
        qty = round(amount / paid) if paid else None
        listed = asks.get(ask_id)
        saved = ((listed - paid) * qty) if (listed and paid and qty) else 0

        out.append({
            "corr": corr,
            "seq": opened.seq,
            "date": str(getattr(opened, "ts", ""))[:10],
            "buying": actor_id == buyer,
            "partner": seller if actor_id == buyer else buyer,
            "item": query.get("text", ""),
            "qty": qty,
            "listed": listed,
            "paid": paid,
            "amount": amount,
            "saved": max(saved, 0),
            "status": ("repaired" if done and done.actor_id == "accountant"
                       else "confirmed" if done else "awaiting"),
            "payment": (done.payload.get("razorpay_payment_id")
                        if done else ""),
            "rounds": [e for e in thread if e.type == "NEGOTIATION_ROUND"],
            "refusals": [e for e in thread if e.type == "POLICY_DECIDED"
                         and e.payload.get("verdict") != "ALLOW"],
        })
    return sorted(out, key=lambda d: d["seq"])


def merchant_sheet(events, actor_id: str, names=None) -> Sheet:
    """Everything one business should see about its own agent.

    `names` maps an actor id to the business's real trading name — "Third
    Wave Bengaluru" rather than `bl_thirdwave`. The log stores ids because a
    log has to; a merchant opening its own books should never meet one. Where
    a name is missing the id is cleaned up rather than printed raw, so a
    business that joined after the roster was written still reads as a name.
    """
    who = _namer(names)
    deals = _deals(events, actor_id)
    books = entries_for(events, actor_id)
    bought = [d for d in deals if d["buying"]]
    saved = _rupees(sum(d["saved"] for d in bought))
    partners = {d["partner"] for d in deals if d["partner"]}

    lessons = {e.payload.get("counterparty_id"): e.payload
               for e in events if e.type == "LESSON_CONSOLIDATED"
               and e.actor_id == actor_id}

    cards = [
        ("Spent through Razorpay", books.bought_inr, "money"),
        ("Saved by negotiating", saved, "good"),
        ("Confirmed by Razorpay", books.settled_inr, "money"),
        ("Still to clear", round(books.bought_inr - books.settled_inr, 2),
         "warn"),
        ("Deals done", len(deals), "count"),
        ("Partners found", len(partners), "count"),
    ]

    # The tab's name IS the business's name. A tab called `bl_thirdwave`
    # sitting in a merchant's own workbook reads as a database table.
    name = who(actor_id)
    return Sheet(
        key=name[:99] or actor_id,
        title=name,
        subtitle="What your agent did on your behalf. Every figure is read "
                 "from the exchange's audit trail — nothing was typed by hand.",
        cards=cards,
        blocks=[_deals_block(deals, who), _talks_block(deals, who),
                _partners_block(deals, lessons, who), _stopped_block(deals)],
    )


def _deals_block(deals, who=None) -> Block:
    who = who or _namer(None)
    rows = []
    for d in deals:
        rows.append([
            d["date"], "bought" if d["buying"] else "sold",
            who(d["partner"]), d["item"][:70], d["qty"],
            _rupees(d["listed"]) if d["listed"] else "",
            _rupees(d["paid"]) if d["paid"] else "",
            _rupees(d["saved"]) if d["saved"] else "",
            _rupees(d["amount"]), d["status"], d["payment"],
        ])
    return Block(
        heading="Your deals",
        note="What your agent bought and sold, what the seller was asking, "
             "and what it actually paid.",
        headings=("Date", "Direction", "Partner", "What", "Qty", "Listed at",
                  "You paid", "You saved", "Total", "Status",
                  "Razorpay payment"),
        rows=rows,
        widths=(96, 88, 150, 300, 62, 104, 104, 104, 112, 104, 180),
        money=("Listed at", "You paid", "You saved", "Total"),
        counts=("Qty",), ident=("Razorpay payment",),
        rules=(("Status", "confirmed", "good"), ("Status", "repaired", "info"),
               ("Status", "awaiting", "warn")),
        empty="No deals yet. Your agent posts here the moment one settles.",
    )


def _talks_block(deals, who=None, limit: int = 40) -> Block:
    who = who or _namer(None)
    rows = []
    for d in deals:
        for e in d["rounds"]:
            rows.append([who(d["partner"]) if e.actor_id != d["partner"]
                         else who(e.actor_id),
                         d["item"][:44], who(e.actor_id),
                         _rupees(e.payload.get("price")),
                         plain.people(plain.money_words(
                             _spoken(e.payload.get("message"))), who)[:260]])
    rows = [[r[1], r[2], r[3], r[4]] for r in rows][:limit]
    return Block(
        heading="How your agent argued for you",
        note="Every offer in the room, quoted. This is the conversation that "
             "produced the price above.",
        headings=("On", "Who spoke", "Offer", "What they said"),
        rows=rows,
        widths=(260, 150, 104, 620),
        money=("Offer",), wrap=("What they said",),
        empty="No haggling was needed — the asking price was already inside "
              "your limit.",
    )


def _partners_block(deals, lessons, who=None) -> Block:
    who = who or _namer(None)
    totals = {}
    for d in deals:
        if not d["partner"]:
            continue
        t = totals.setdefault(d["partner"], {"n": 0, "spend": 0, "saved": 0})
        t["n"] += 1
        t["spend"] += d["amount"]
        t["saved"] += d["saved"]
    rows = []
    for partner, t in sorted(totals.items(), key=lambda kv: -kv[1]["spend"]):
        lesson = lessons.get(partner) or {}
        # An agent files its verdict in paise because that is what it was
        # handed. "They paid the agreed 19500 per unit" is its own sentence
        # and it is right — it is just not in the units a merchant reads.
        rows.append([who(partner), t["n"], _rupees(t["spend"]),
                     _rupees(t["saved"]),
                     plain.people(
                         plain.money_words(lesson.get("text", "")), who)[:260]
                     or "No verdict yet — too few dealings to judge."])
    return Block(
        heading="Who you are dealing with",
        note="Your agent's own read on each partner, written after the money "
             "moved.",
        headings=("Partner", "Deals", "Value", "Saved",
                  "What your agent makes of them"),
        rows=rows,
        widths=(170, 80, 116, 116, 620),
        money=("Value", "Saved"), counts=("Deals",),
        wrap=("What your agent makes of them",),
        empty="No partners yet.",
    )


def _stopped_block(deals) -> Block:
    """What the gate refused, and what the agent did next.

    A merchant sees this as protection rather than as failure, which is what
    it is: the cap held, the agent came back smaller, and the money still
    moved.
    """
    rows = []
    for d in deals:
        for e in d["refusals"]:
            # The amount the gate weighed sits in what it evaluated, not at
            # the top of the ruling — the column read empty for every refusal
            # while the figure was in the payload all along.
            asked = (e.payload.get("amount")
                     or (e.payload.get("limits_evaluated") or {}).get("amount"))
            rows.append([d["item"][:56],
                         _rupees(asked) if asked else "",
                         plain.gate_reason(e.payload),
                         (f'Retried smaller and settled at '
                          f'{plain.rupees(d["amount"])}'
                          if d["paid"] else "Did not proceed")])
    return Block(
        heading="What the gate stopped",
        note="Every refusal is recorded before any money moves. A first deal "
             "with an unknown supplier is capped on purpose.",
        headings=("On", "Amount asked", "Why it was refused", "What happened next"),
        rows=rows,
        widths=(260, 124, 500, 300),
        money=("Amount asked",),
        wrap=("Why it was refused", "What happened next"),
        rules=(("Why it was refused", "over the", "warn"),),
        empty="Nothing was refused. Every action your agent took was inside "
              "the limits you set.",
    )
