"""What a category actually clears at — the lot only the processor can mint.

WHY THIS EXISTS. The first thing this exchange put up for auction was a
sentence: "micro-payment adoption is expanding, driven by a fragmentation of
one-off, low-value settlements across a broadening merchant base." It could
have been written without looking at any data, and watching eight agents bid
real points on it made the auction look like theatre rather than a market.

A merchant will pay for something it can act on tomorrow. This is that:

    clears     the median price this category actually settles at
    ask        the median price sellers open at
    below_ask  the share of trades that closed under the seller's ask
    saving     the median discount, when there was one

Read together those say something no merchant can work out alone. Cold brew
clears at 195 against a 210 ask and half of buyers get a discount, so a buyer
paying the ask is leaving money on the table and should push. Electronics
assembly closes at the ask every single time, so pushing there wastes the one
thing an agent cannot buy more of — the counterparty's patience.

A SELLER SEES ITS OWN ASKS AND A BUYER SEES ITS OWN BILLS. Only the party
that settles both sides of every trade can say what the middle is. That is
the entire reason this is Razorpay's to sell and nobody else's.

THE FLOOR BINDS HERE TOO. A benchmark drawn from two merchants is those two
merchants' pricing published to their competitors. `check_privacy` is applied
per category, on DISTINCT merchants, and a category below the floor is
refused with its count recorded.

NO MODEL AND NO NETWORK. Every figure is arithmetic over the log; a test
asserts it. There is nothing here to hallucinate.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from exchange import events as ev
from exchange.house.campaigns import is_board_row
from exchange.house.insights import HOUSE_ACTOR_ID, check_privacy


@dataclass(frozen=True)
class Fill:
    """One matched trade, with the ask it was struck against."""
    category: str
    buyer: str
    seller_order: str
    clearing_paise: int
    ask_paise: int

    @property
    def below_ask(self) -> bool:
        return self.clearing_paise < self.ask_paise

    @property
    def saving(self) -> float:
        if self.ask_paise <= 0 or not self.below_ask:
            return 0.0
        return (self.ask_paise - self.clearing_paise) / self.ask_paise


@dataclass
class Benchmark:
    category: str
    fills: list = field(default_factory=list)
    merchants: set = field(default_factory=set)

    @property
    def trades(self) -> int:
        return len(self.fills)

    @property
    def clears_paise(self) -> int:
        return int(statistics.median(f.clearing_paise for f in self.fills))

    @property
    def ask_paise(self) -> int:
        return int(statistics.median(f.ask_paise for f in self.fills))

    @property
    def below_ask_share(self) -> float:
        return sum(1 for f in self.fills if f.below_ask) / self.trades

    @property
    def median_saving(self) -> float:
        """The discount when there was one.

        Deliberately NOT averaged over the trades that paid full price. A
        median that includes every zero says the category barely discounts,
        when what a buyer needs to know is how much is on the table in the
        cases where the seller does move.
        """
        wins = [f.saving for f in self.fills if f.below_ask]
        return statistics.median(wins) if wins else 0.0

    @property
    def k(self) -> int:
        return len(self.merchants)


@dataclass(frozen=True)
class Refusal:
    category: str
    k: int
    reason: str


def observe(events):
    """Every match that can be priced against the ask it was struck on.

    A match with no reachable ask is skipped rather than guessed at: the
    whole figure being sold is the gap between the two, and inventing one
    half of it would be inventing the finding.
    """
    asks, need_of, category_of = {}, {}, {}

    for event in events:
        payload = event.payload or {}
        if event.type == ev.ORDER_POSTED:
            if payload.get("side") == "ASK" and payload.get("limit_price"):
                asks[payload.get("order_id")] = payload["limit_price"]
        elif event.type == ev.TURN_ENDED:
            need_of[event.correlation_id] = payload.get("need", "")
        elif event.type == ev.CAMPAIGN_RANKED and is_board_row(event):
            for need in payload.get("needs", []):
                category_of[need] = payload.get("campaign", "")

    fills = []
    for event in events:
        if event.type != ev.MATCH_PROPOSED:
            continue
        payload = event.payload or {}
        ask = asks.get(payload.get("ask_order_id"))
        clearing = payload.get("clearing_price")
        category = category_of.get(need_of.get(event.correlation_id, ""))
        if not (ask and clearing and category):
            continue
        fills.append(Fill(category=category, buyer=event.actor_id,
                          seller_order=payload.get("ask_order_id", ""),
                          clearing_paise=clearing, ask_paise=ask))
    return fills


def rank(fills, floor=None):
    """Group into per-category benchmarks. Arithmetic only.

    Ordered by how many trades stand behind each one, because a benchmark's
    worth is its evidence: a median over twenty-five fills is a market price
    and a median over three is an anecdote wearing the same clothes.
    """
    grouped: dict[str, Benchmark] = {}
    for fill in fills:
        row = grouped.setdefault(fill.category, Benchmark(category=fill.category))
        row.fills.append(fill)
        row.merchants.add(fill.buyer)

    ranked, refused = [], []
    for row in grouped.values():
        verdict = (check_privacy(row.merchants) if floor is None
                   else check_privacy(row.merchants, k_min=floor))
        if not verdict.allowed:
            refused.append(Refusal(row.category, verdict.k, verdict.reason))
            continue
        ranked.append(row)

    ranked.sort(key=lambda r: (-r.trades, r.category))
    refused.sort(key=lambda r: r.category)
    return ranked, refused


def playbook(rows) -> list:
    """The paid half: the numbers themselves."""
    return [{
        "category": row.category,
        "trades": row.trades,
        "merchants": row.k,
        "clears_paise": row.clears_paise,
        "ask_paise": row.ask_paise,
        "below_ask_share": round(row.below_ask_share, 4),
        "median_saving": round(row.median_saving, 4),
    } for row in rows]


def headline(rows) -> str:
    """The free half: that there is a gap, never how wide or where.

    Written rather than generated. A model asked for a teaser produces
    something that sounds like a teaser; what is wanted is one true sentence
    that is useless without the detail, and arithmetic can guarantee that
    where a sentence generator cannot.
    """
    if not rows:
        return "No category has enough trades behind it to price yet."
    movers = sum(1 for r in rows if r.below_ask_share > 0)
    return (f"{len(rows)} categories now have a clearing price of their own, "
            f"and in {movers} of them sellers are settling below their own "
            f"ask. Which ones, and by how much, is in the detail.")


def publish(log, rows, refused, correlation_id: str) -> None:
    for refusal in refused:
        log.append(HOUSE_ACTOR_ID, ev.PRIVACY_REFUSED, {
            "scope": "price_benchmark",
            "campaign": refusal.category,
            "k": refusal.k,
            "reason": refusal.reason,
        }, correlation_id=correlation_id)

    for position, row in enumerate(rows, start=1):
        log.append(HOUSE_ACTOR_ID, ev.BENCHMARK_PUBLISHED, {
            "audience": "razorpay_internal",
            "scope": "price_benchmark",
            "rank": position,
            "category": row.category,
            "trades": row.trades,
            "merchants": row.k,
            "clears_paise": row.clears_paise,
            "ask_paise": row.ask_paise,
            "below_ask_share": round(row.below_ask_share, 4),
            "median_saving": round(row.median_saving, 4),
        }, correlation_id=correlation_id)
