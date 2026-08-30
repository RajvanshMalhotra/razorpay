"""The whole market as a workbook of tables.

The books tab answers "what did my agent buy". This answers everything else
the agents actually did: every offer they made and the words they made it in,
every ruling the gate handed down, who chose whom and why, what the house
ranked, what the auction fetched, and what each broker decided to remember.

WHY IT IS SHAPED AS TABLES RATHER THAN AS PAGES. A `Table` carries its own
headings, widths, which columns are money and which are identifiers, and
which values should be coloured. The formatter then knows how to render any
table without knowing what it contains — so a new tab is a function returning
rows, not another hundred lines of formatting requests.

Every row is read from the log. Nothing here is summarised into a number
whose derivation is not also on the sheet.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from exchange.books import entries_for


@dataclass
class Table:
    """One tab: what it is called, what is in it, and how to read it."""
    key: str
    title: str
    subtitle: str
    headings: tuple
    rows: list
    widths: tuple
    money: tuple = ()        # headings shown as rupees
    counts: tuple = ()       # headings shown as plain integers
    ident: tuple = ()        # headings that are ids: small, grey, monospaced
    wrap: tuple = ()         # headings holding prose
    rules: tuple = ()        # (heading, contains, tone) -> coloured text
    summary: list = field(default_factory=list)   # (label, value, is_money)


def _rupees(paise) -> float:
    return round((paise or 0) / 100, 2)


def _actor(name: str) -> str:
    return (name or "")[2:].replace("_", " ") if str(name).startswith("m_") \
        else (name or "")


def _spoken(message) -> str:
    """What the agent said, without the price it already said in numbers.

    The model prefixes its own offer — "PRICE: 20000 \nStarting with..." —
    and the price is already its own column, in rupees. Left in, the row
    shows 200.00 beside 20000 and reads like a contradiction.
    """
    import re

    text = " ".join(str(message or "").split())
    text = re.sub(r"^PRICE:\s*[\d,.]+\s*[-—:]?\s*", "", text, flags=re.I)
    return text.strip()


def overview(events, roster) -> Table:
    """The front page: the market in totals, then every business in it."""
    books = [entries_for(events, a) for a in roster]
    live = [b for b in books if b.entries]

    settled = sum(1 for e in events if e.type == "SETTLEMENT_COMPLETED")

    # BOTH SIDES OF A TRADE KEEP IT IN THEIR OWN BOOKS, so summing every
    # merchant's confirmed total counts each payment twice — it reported
    # 147,672 confirmed against 139,903 committed, which cannot happen. The
    # market total comes from the settlements themselves, once each.
    opened = {e.payload.get("settlement_id"): e.payload.get("amount", 0)
              for e in events if e.type == "SETTLEMENT_INITIATED"}
    done = {e.payload.get("settlement_id") for e in events
            if e.type == "SETTLEMENT_COMPLETED"}
    committed = _rupees(sum(opened.values()))
    confirmed = _rupees(sum(v for k, v in opened.items() if k in done))
    allowed = sum(1 for e in events if e.type == "POLICY_DECIDED"
                  and e.payload.get("verdict") == "ALLOW")
    refused = sum(1 for e in events if e.type == "POLICY_DECIDED"
                  and e.payload.get("verdict") != "ALLOW")
    repaired = sum(1 for e in events if e.type == "SETTLEMENT_COMPLETED"
                   and e.actor_id == "accountant")
    offers = sum(1 for e in events if e.type == "NEGOTIATION_ROUND")
    walked = sum(1 for e in events if e.type == "NEGOTIATION_ENDED"
                 and not e.payload.get("agreed"))

    rows = []
    for b in sorted(live, key=lambda x: -x.bought_inr):
        awaiting = round(sum(e.amount_inr for e in b.entries
                             if e.status == "pending"), 2)
        rows.append([_actor(b.actor_id).title(), b.bought_inr, b.sold_inr,
                     b.net_inr, b.settled_inr, awaiting, len(b.entries),
                     len({e.counterparty for e in b.entries if e.counterparty})])

    return Table(
        key="Overview",
        title="Agent Exchange — the market",
        subtitle="Every figure is counted from the exchange's audit trail. "
                 "Nothing on any tab was typed by hand.",
        headings=("Business", "Purchases", "Sales", "Net",
                  "Confirmed by Razorpay", "Awaiting", "Trades",
                  "Counterparties"),
        rows=rows,
        widths=(190, 116, 106, 116, 172, 116, 84, 118),
        money=("Purchases", "Sales", "Net", "Confirmed by Razorpay",
               "Awaiting"),
        counts=("Trades", "Counterparties"),
        summary=[
            ("Businesses trading", len(roster), False),
            ("Committed through Razorpay", committed, True),
            ("Confirmed by Razorpay", confirmed, True),
            ("Payments completed", settled, False),
            ("Offers exchanged between agents", offers, False),
            ("Negotiations that ended without a deal", walked, False),
            ("Money actions allowed", allowed, False),
            ("Money actions refused", refused, False),
            ("Payment mismatches repaired unattended", repaired, False),
        ],
    )


def negotiations(events, limit: int = 400) -> Table:
    """Every offer, and the sentence the agent made it in."""
    rows = []
    for e in events:
        if e.type != "NEGOTIATION_ROUND":
            continue
        said = _spoken(e.payload.get("message"))
        rows.append([e.seq, _actor(e.actor_id),
                     _rupees(e.payload.get("price")), said[:300],
                     e.correlation_id])
    return Table(
        key="Negotiations",
        title="Every offer the agents made",
        subtitle="Quoted, not summarised. The price is what they offered; "
                 "the words are their own.",
        headings=("Event", "Agent", "Offer", "What they said", "Trade id"),
        rows=rows[:limit],
        widths=(70, 160, 104, 640, 250),
        money=("Offer",), ident=("Event", "Trade id"),
        wrap=("What they said",),
    )


def gate_decisions(events) -> Table:
    """Every ruling, including the ones that said yes."""
    rows = []
    for e in events:
        if e.type != "POLICY_DECIDED":
            continue
        p = e.payload
        rows.append([e.seq, p.get("verdict", ""),
                     _rupees(p.get("amount")) if p.get("amount") else "",
                     str(p.get("reason", ""))[:300], e.correlation_id])
    return Table(
        key="Gate decisions",
        title="Every money action, ruled on before it happened",
        subtitle="A decision is recorded even when the answer is yes — a gate "
                 "you only hear from when it refuses is not auditable.",
        headings=("Event", "Verdict", "Amount", "Reason", "Trade id"),
        rows=rows,
        widths=(70, 96, 116, 620, 250),
        money=("Amount",), ident=("Event", "Trade id"), wrap=("Reason",),
        rules=(("Verdict", "DENY", "bad"), ("Verdict", "ALLOW", "good")),
    )


def counterparties(events) -> Table:
    """Who each agent picked, and the reason it gave for picking them."""
    seller_of = {e.payload.get("order_id"): e.actor_id
                 for e in events if e.type == "ORDER_POSTED"}
    rows = []
    for e in events:
        if e.type != "COUNTERPARTY_CHOSEN":
            continue
        p = e.payload
        rows.append([e.seq, _actor(e.actor_id),
                     _actor(seller_of.get(p.get("ask_order_id"), "")),
                     len(p.get("shortlist") or ()),
                     str(p.get("reason", ""))[:300]])
    return Table(
        key="Who dealt with whom",
        title="Every counterparty an agent chose",
        subtitle="Nobody made an introduction. The reason is the agent's own, "
                 "recorded at the moment it decided.",
        headings=("Event", "Agent", "Chose", "Shortlist", "Why"),
        rows=rows,
        widths=(70, 160, 160, 90, 620),
        counts=("Shortlist",), ident=("Event",), wrap=("Why",),
    )


def campaign_board(events) -> Table:
    """What the house ranked, and the press it cited for each row."""
    rows = []
    for e in events:
        if e.type != "CAMPAIGN_RANKED":
            continue
        p = e.payload
        sources = ", ".join(s.get("publisher") or s.get("title", "")[:30]
                            for s in (p.get("sources") or [])[:6])
        rows.append([p.get("rank"), p.get("campaign"),
                     round(p.get("movement") or 0, 2), p.get("merchants"),
                     _rupees(p.get("value_paise")),
                     str(p.get("driver", ""))[:300], sources])
    return Table(
        key="Campaign board",
        title="Trending campaigns across the client base",
        subtitle="Razorpay internal. The ranking is arithmetic over this log; "
                 "the explanation comes from the public press and carries its "
                 "source.",
        headings=("Rank", "Campaign", "Movement", "Businesses", "Value",
                  "What is driving it", "Sources"),
        rows=rows,
        widths=(64, 210, 100, 106, 116, 460, 300),
        money=("Value",), counts=("Rank", "Businesses"),
        wrap=("What is driving it", "Sources"),
    )


def auction(events) -> Table:
    """The sealed bids, and what the winner actually paid."""
    minted = next((e for e in events if e.type == "INSIGHT_MINTED"), None)
    if minted is None:
        return Table(key="Auction", title="Auction", subtitle="No lot minted.",
                     headings=("Bidder",), rows=[], widths=(200,))
    corr = minted.correlation_id
    thread = [e for e in events if e.correlation_id == corr]
    bids = sorted((e for e in thread if e.type == "BID_PLACED"),
                  key=lambda e: e.payload.get("amount", 0), reverse=True)
    cleared = next((e for e in thread if e.type == "AUCTION_CLEARED"), None)
    royalties = [e for e in thread if e.type == "CREDITS_TRANSFERRED"
                 and e.payload.get("from_actor_id") == "house"]
    winner = cleared.payload.get("winner_id") if cleared else ""
    price = cleared.payload.get("price") if cleared else 0

    rows = []
    for n, e in enumerate(bids):
        reason = str(e.payload.get("reason", "")).strip()
        reason = reason.split("\n", 1)[-1].strip()
        rows.append([n + 1, _actor(e.actor_id), e.payload.get("amount"),
                     "won" if e.actor_id == winner else "",
                     reason[:300]])
    return Table(
        key="Auction",
        title="The intelligence lot, sold by sealed bid",
        subtitle=str(minted.payload.get("headline", "")),
        headings=("Rank", "Bidder", "Bid (points)", "Outcome",
                  "Why they valued it there"),
        rows=rows,
        widths=(64, 170, 116, 96, 620),
        counts=("Rank", "Bid (points)"),
        wrap=("Why they valued it there",),
        rules=(("Outcome", "won", "good"),),
        summary=[
            ("Bids received", len(bids), False),
            ("Winner", _actor(winner), False),
            ("Paid, at the runner-up's price", price, False),
            ("Businesses that contributed to the insight",
             len(minted.payload.get("contributor_ids") or ()), False),
            ("Royalty paid to each of them",
             royalties[0].payload.get("amount") if royalties else 0, False),
        ],
    )


def lessons(events) -> Table:
    """What each broker decided was worth remembering."""
    rows = []
    for e in events:
        if e.type != "LESSON_CONSOLIDATED":
            continue
        p = e.payload
        rows.append([e.seq, _actor(e.actor_id),
                     _actor(p.get("counterparty_id")), p.get("kind", ""),
                     str(p.get("text", ""))[:300]])
    return Table(
        key="What agents learned",
        title="What each broker decided to remember",
        subtitle="A reliability lesson can change a counterparty's standing "
                 "and its spending cap. A behavioural one never can.",
        headings=("Event", "Agent", "About", "Kind", "Lesson"),
        rows=rows,
        widths=(70, 160, 160, 116, 620),
        ident=("Event",), wrap=("Lesson",),
        rules=(("Kind", "reliability", "info"),),
    )


def merchant_table(books) -> Table:
    """One business's own books."""
    from exchange.books import HEADINGS

    return Table(
        key=books.actor_id[2:][:99] or books.actor_id,
        title=_actor(books.actor_id).title(),
        subtitle="Books kept automatically from the exchange audit trail.",
        headings=HEADINGS,
        rows=[e.row() for e in books.entries],
        widths=(96, 88, 156, 300, 66, 106, 112, 96, 168, 180, 180, 66, 260),
        money=("Unit price", "Amount"), counts=("Qty",),
        ident=("Razorpay order", "Razorpay payment", "Event", "Trade id"),
        rules=(("Status", "settled", "good"), ("Status", "repaired", "info"),
               ("Status", "pending", "warn"), ("Gate ruling", "DENY", "bad")),
        summary=[(l.replace(" (₹)", ""), v, "(₹)" in l)
                 for l, v in books.summary()],
    )


def market_tables(events, roster) -> list:
    """Every market-wide tab, in the order the workbook should read."""
    return [overview(events, roster), campaign_board(events), auction(events),
            negotiations(events), gate_decisions(events),
            counterparties(events), lessons(events)]
