"""What each campaign actually earned, read from settlements.

WHY THIS IS THE CHEAPEST BOARD AND THE MOST DEFENSIBLE ONE.

The campaign board reads the press. The brand radar reads Reddit and X. Both
cost money per category per run, both depend on somebody else's API being up,
and neither can tell you whether a campaign SOLD anything — they measure
talk. Talk is a proxy, and a proxy is what you use when you cannot see the
real thing.

Razorpay can see the real thing. It issues the payment link and it watches the
capture, so it knows, natively and for free, how many people were asked to pay
and how many did. No scraping, no credits, no third party. That is the asset
nobody else on the internet has, and measuring it costs one pass over a log
that has already been written.

    links      payment links issued for this campaign
    paid       links that reached a captured payment
    revenue    what actually settled, in paise
    aov        revenue / paid
    stopped    gate refusals on this campaign's trades

`settled_share` is paid over links. Read the note below before putting it on
a page: it is a fact about this run, not a demand signal.

EVERY FIGURE IS ARITHMETIC OVER THE LOG. No model is called and no page is
fetched — a test asserts it. The join from a payment back to a campaign is the
board's own published `needs` list, so this reads what was already decided
rather than deciding it again with a second model call that might disagree.

WHAT "PAID" MEANS HERE, AND WHY IT IS NOT A CONVERSION RATE.

A payment link in this run was settled by `scripts/market/pay.py`, which drives
Razorpay's test-mode checkout and clicks Success on the mock bank. It pays ONE
LINK PER MERCHANT by default, because test mode allows thirty links per account
and the privacy floor counts distinct merchants — a second payment for a
merchant that already has one spends a capped resource another merchant needs.

Measured: 23 links issued, 17 settled, and those 17 belong to 17 distinct
merchants. Every unpaid link is the script declining to spend, not a buyer
declining to pay. So the settled-over-issued ratio measures the harness, and
calling it a conversion rate would put a number on the page that a reader
would take as demand and that means nothing of the kind.

The ratio is still reported, because it is a true fact about the run and
hiding it would be worse. It is labelled as what it is, and the page says
which links were left deliberately unpaid.

WHAT IS REAL HERE. The revenue is real money moved on real Razorpay test-mode
payment ids. The average order value is real. The gate refusals are real
rulings the gate actually made. Those three are the board.

WHAT NOBODY CAN SEE YET. Razorpay watches the payment, not the ad click. It
knows a link was issued and paid; it does not know the buyer came from an
Instagram ad rather than a shop window. Real ad-to-sale attribution needs the
campaign tag to travel with the link — Razorpay's `notes` field on an order
carries exactly that — and until a merchant tags its links this measures
campaign-to-cash, which is a smaller claim and an honest one.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from exchange import events as ev
from exchange.house.campaigns import is_board_row
from exchange.house.insights import HOUSE_ACTOR_ID


@dataclass(frozen=True)
class Attempt:
    """One merchant's turn, followed through to whether the money landed."""
    campaign: str
    correlation_id: str
    amount: int
    link_issued: bool
    captured: bool
    refused: bool
    seconds_to_pay: int | None = None


@dataclass
class Row:
    campaign: str
    attempts: list = field(default_factory=list)

    @property
    def tried(self) -> int:
        return len(self.attempts)

    @property
    def links(self) -> int:
        return sum(1 for a in self.attempts if a.link_issued)

    @property
    def paid(self) -> int:
        return sum(1 for a in self.attempts if a.captured)

    @property
    def stopped(self) -> int:
        return sum(1 for a in self.attempts if a.refused)

    @property
    def settled_share(self) -> float:
        """Settled over issued. NOT a conversion rate — see the module note:
        the unpaid links are the payment script declining to spend, not
        buyers declining to pay.

        Zero links is 0.0, never a division by zero and never 100%: a campaign
        nobody was asked to pay for settled nothing, and reporting that as
        perfect would be the most flattering possible lie.
        """
        return (self.paid / self.links) if self.links else 0.0

    @property
    def revenue_paise(self) -> int:
        return sum(a.amount for a in self.attempts if a.captured)

    @property
    def aov_paise(self) -> int:
        return (self.revenue_paise // self.paid) if self.paid else 0

    @property
    def median_seconds(self) -> int | None:
        times = sorted(a.seconds_to_pay for a in self.attempts
                       if a.seconds_to_pay is not None)
        return times[len(times) // 2] if times else None


def _seconds(start: str, end: str) -> int | None:
    try:
        a = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.datetime.fromisoformat(end.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return int((b - a).total_seconds())


def need_to_campaign(events) -> dict:
    """Which campaign each need phrase belongs to, from the published board.

    Read rather than recomputed. Asking a model to group the phrases a second
    time would produce a grouping that disagrees with the board sitting above
    it on the same page, and two boards that disagree about the same trades
    are worse than one board.
    """
    mapping = {}
    for event in events:
        if event.type != ev.CAMPAIGN_RANKED or not is_board_row(event):
            continue
        name = event.payload.get("campaign", "")
        for need in event.payload.get("needs", []):
            mapping[need] = name
    return mapping


def observe(events):
    """Follow every turn through to whether the money landed.

    Returns (attempts, unmatched). `unmatched` counts turns whose need phrase
    is on no published row — reported rather than dropped, because a board
    that quietly measures two thirds of the trading looks exactly like a board
    that measured all of it.
    """
    mapping = need_to_campaign(events)
    if not mapping:
        return [], 0

    by_corr: dict[str, dict] = {}
    for event in events:
        slot = by_corr.setdefault(event.correlation_id, {})
        payload = event.payload or {}
        if event.type == ev.TURN_ENDED:
            slot["need"] = payload.get("need", "")
        elif event.type == ev.SETTLEMENT_INITIATED:
            slot["amount"] = payload.get("amount", 0)
            slot["settlement_id"] = payload.get("settlement_id")
            slot["link"] = bool(payload.get("payment_link_id"))
            slot["initiated_at"] = event.ts
        elif event.type == ev.SETTLEMENT_COMPLETED:
            slot["captured"] = True
            slot["completed_at"] = event.ts
        elif event.type == ev.POLICY_DECIDED:
            if payload.get("verdict") == "DENY":
                slot["refused"] = True

    attempts, unmatched = [], 0
    for corr, slot in by_corr.items():
        need = slot.get("need")
        if not need:
            continue
        campaign = mapping.get(need)
        if campaign is None:
            unmatched += 1
            continue
        seconds = None
        if slot.get("initiated_at") and slot.get("completed_at"):
            seconds = _seconds(slot["initiated_at"], slot["completed_at"])
        attempts.append(Attempt(
            campaign=campaign,
            correlation_id=corr,
            amount=slot.get("amount", 0),
            link_issued=bool(slot.get("link")),
            captured=bool(slot.get("captured")),
            refused=bool(slot.get("refused")),
            seconds_to_pay=seconds,
        ))
    return attempts, unmatched


def rank(attempts):
    """Order campaigns by what they actually earned. Pure arithmetic.

    Revenue first and settled share second, because a campaign that converts
    perfectly on two small orders has not outperformed one that converts
    two-thirds of forty large ones — and a board sold to merchants that says
    otherwise is selling them a mistake.
    """
    grouped: dict[str, Row] = {}
    for attempt in attempts:
        grouped.setdefault(attempt.campaign, Row(campaign=attempt.campaign))
        grouped[attempt.campaign].attempts.append(attempt)

    rows = list(grouped.values())
    rows.sort(key=lambda r: (-r.revenue_paise, -r.settled_share, r.campaign))
    return rows


def publish(log, rows, unmatched: int, correlation_id: str) -> None:
    for position, row in enumerate(rows, start=1):
        log.append(HOUSE_ACTOR_ID, ev.CAMPAIGN_PERFORMANCE, {
            "audience": "razorpay_internal",
            "scope": "performance",
            "rank": position,
            "campaign": row.campaign,
            "tried": row.tried,
            "links": row.links,
            "paid": row.paid,
            "stopped": row.stopped,
            # Named for what it measures. "conversion" would be read as demand.
            "settled_share": round(row.settled_share, 4),
            "revenue_paise": row.revenue_paise,
            "aov_paise": row.aov_paise,
            "median_seconds_to_pay": row.median_seconds,
            # Carried on every row so a reader can see how much of the
            # trading this board is actually speaking for.
            "unmatched_turns": unmatched,
        }, correlation_id=correlation_id)
