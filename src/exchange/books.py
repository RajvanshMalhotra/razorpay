"""The books: every buy and sell, as a business would keep them.

An agent that spends a merchant's money owes that merchant a ledger. Not a
tape of events — a ledger, with dates, counterparties, unit prices, amounts,
and the payment id the bank gave back, in the columns an accountant already
knows how to read.

BOTH SIDES OF EVERY TRADE. A merchant is a buyer on its own turn threads and
a seller whenever somebody else's agent bought from its listing. Reporting
only the first would show a business that spends and never earns, which is
the wrong shape entirely: `entries_for` walks the whole log and picks up both.

WHAT IS NOT HERE. No tax, no invoice numbers, no accounting period, no
opening balance. Those are real bookkeeping and this is a market log; a
column called GST filled from nothing would be worse than no column. What is
here is what the log can actually answer for, and every row carries the
correlation id and event sequence that back it.

PENDING IS NOT PAID. A settlement that Razorpay has not confirmed shows as
`pending` and is excluded from the settled totals. The whole reason this
project has an accountant is that the difference matters.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# The header a spreadsheet gets, in the order a reader expects to meet it.
COLUMNS = (
    "date", "direction", "counterparty", "item", "qty", "unit_price_inr",
    "amount_inr", "status", "gate", "razorpay_order_id",
    "razorpay_payment_id", "event", "correlation_id",
)


@dataclass
class Entry:
    date: str = ""
    direction: str = ""          # bought | sold
    counterparty: str = ""
    item: str = ""
    qty: int | None = None
    unit_price_inr: float | None = None
    amount_inr: float = 0.0
    status: str = ""             # settled | pending | repaired
    gate: str = ""               # ALLOW, or DENY then ALLOW
    razorpay_order_id: str = ""
    razorpay_payment_id: str = ""
    event: int = 0
    correlation_id: str = ""

    def row(self) -> list:
        data = asdict(self)
        return [("" if data[c] is None else data[c]) for c in COLUMNS]


@dataclass
class Books:
    actor_id: str = ""
    entries: list = field(default_factory=list)

    @property
    def bought_inr(self) -> float:
        return round(sum(e.amount_inr for e in self.entries
                         if e.direction == "bought"), 2)

    @property
    def sold_inr(self) -> float:
        return round(sum(e.amount_inr for e in self.entries
                         if e.direction == "sold"), 2)

    @property
    def settled_inr(self) -> float:
        """Money the bank has confirmed, in either direction.

        Kept separate from the gross figures on purpose. A merchant reading
        its own books needs to see committed and confirmed as two numbers,
        because for a while they are genuinely different and the gap is where
        every reconciliation problem lives.
        """
        return round(sum(e.amount_inr for e in self.entries
                         if e.status in ("settled", "repaired")), 2)

    @property
    def net_inr(self) -> float:
        return round(self.sold_inr - self.bought_inr, 2)

    def summary(self) -> list[tuple[str, object]]:
        pending = [e for e in self.entries if e.status == "pending"]
        refused = [e for e in self.entries if "DENY" in e.gate]
        counterparties = {e.counterparty for e in self.entries if e.counterparty}
        return [
            ("Merchant", self.actor_id),
            ("Purchases (₹)", self.bought_inr),
            ("Sales (₹)", self.sold_inr),
            ("Net (₹)", self.net_inr),
            ("Confirmed by Razorpay (₹)", self.settled_inr),
            ("Awaiting confirmation (₹)",
             round(sum(e.amount_inr for e in pending), 2)),
            ("Transactions", len(self.entries)),
            ("Awaiting confirmation", len(pending)),
            ("Refused once by the gate", len(refused)),
            ("Counterparties dealt with", len(counterparties)),
        ]


def entries_for(events, actor_id: str) -> Books:
    """Every trade this merchant was on either side of."""
    by_corr: dict[str, list] = {}
    for event in events:
        by_corr.setdefault(event.correlation_id, []).append(event)

    # Which merchant owns each ask, so the seller of a trade can be named.
    seller_of = {e.payload.get("order_id"): e.actor_id
                 for e in events if e.type == "ORDER_POSTED"}
    titles = {e.payload.get("asset_id"): e.payload.get("title", "")
              for e in events if e.type == "ASSET_LISTED"}

    books = Books(actor_id=actor_id)
    for corr, thread in by_corr.items():
        if not (corr.startswith("turn_") or corr.startswith("shop_")):
            continue
        opened = next((e for e in thread if e.type == "SETTLEMENT_INITIATED"),
                      None)
        if opened is None or opened.payload.get("currency") != "INR":
            continue

        buyer = opened.actor_id
        matched = next((e for e in thread if e.type == "MATCH_PROPOSED"), None)
        chosen = next((e for e in thread if e.type == "COUNTERPARTY_CHOSEN"),
                      None)
        ask = ((chosen.payload.get("ask_order_id") if chosen else None)
               or (matched.payload.get("ask_order_id") if matched else None))
        seller = seller_of.get(ask, "")

        if actor_id not in (buyer, seller):
            continue

        done = next((e for e in thread if e.type == "SETTLEMENT_COMPLETED"),
                    None)
        repaired = bool(done and done.actor_id == "accountant")
        posted = next((e for e in thread if e.type == "ORDER_POSTED"), None)
        query = (posted.payload.get("asset_query") or {}) if posted else {}
        item = query.get("text") or titles.get(
            (posted.payload.get("asset_ref") if posted else None), "")

        verdicts = [e.payload.get("verdict") for e in thread
                    if e.type == "POLICY_DECIDED"]

        # THE PRICE IS AUTHORITATIVE; THE QUANTITY IS DERIVED FROM IT.
        # Taking qty from MATCH_PROPOSED and dividing the amount by it gave
        # 30.47 for a trade agreed at 195.00 — because the gate had capped
        # that trade to a trial size and the match still named the full lot.
        # The agreed price and the amount are the two figures the settlement
        # is actually accountable to, so the third is computed from them and
        # qty × unit == amount always holds.
        amount = (opened.payload.get("amount") or 0) / 100
        ended = next((e for e in thread if e.type == "NEGOTIATION_ENDED"
                      and e.payload.get("agreed")), None)
        paise = (ended.payload.get("final_price") if ended
                 else (matched.payload.get("clearing_price")
                       if matched else None))
        unit = round(paise / 100, 2) if paise else None
        qty = round(amount / unit) if unit else None

        books.entries.append(Entry(
            date=str(opened.ts)[:10] if getattr(opened, "ts", None) else "",
            direction="bought" if actor_id == buyer else "sold",
            counterparty=(seller if actor_id == buyer else buyer),
            item=item,
            qty=qty,
            unit_price_inr=unit,
            amount_inr=round(amount, 2),
            status=("repaired" if repaired else "settled" if done
                    else "pending"),
            gate=" then ".join(verdicts),
            razorpay_order_id=opened.payload.get("razorpay_order_id") or "",
            razorpay_payment_id=(done.payload.get("razorpay_payment_id")
                                 if done else ""),
            event=opened.seq,
            correlation_id=corr,
        ))

    books.entries.sort(key=lambda e: e.event)
    return books


def sheet_rows(books: Books) -> list[list]:
    """The whole workbook as one grid: summary, a blank line, then the ledger.

    One sheet rather than two because a merchant opening this wants the total
    before the detail, and a tab it has to click is a tab it will not click.
    """
    grid = [[label, value] for label, value in books.summary()]
    grid.append([])
    grid.append(list(COLUMNS))
    grid.extend(entry.row() for entry in books.entries)
    return grid
