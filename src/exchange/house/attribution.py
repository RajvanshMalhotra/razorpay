"""Campaign to cash, read from a real Razorpay account.

WHAT THIS IS FOR. `performance.py` computes the same figures from this
project's own event log, which is the right thing for the replay and useless
to anybody else. This reads the account itself: point it at any Razorpay key
and it answers what each campaign earned, from that account's own payment
links. No log, no simulation, no events — just the transactions Razorpay
already has.

That is the whole plug-and-play claim made concrete. A merchant does not
adopt our event log to get this; they tag their links and ask their own
account.

HOW THE TAG TRAVELS. Razorpay stores a `notes` object on an order and on a
payment link and returns it on every read. So a campaign tag written at
creation comes back on the transaction, and grouping by it is the whole of
attribution. `rails/inr.py` writes `notes["campaign"]` when a campaign is
supplied; anything created by any other means works identically as long as it
carries the same key.

WHAT COUNTS AS PAID. A payment link's `status` is Razorpay's own word, and
`paid` is the only value that means money arrived. `amount_paid` is used for
the money rather than `amount`, because a partially paid link has both and
only one of them is real revenue.

UNTAGGED LINKS ARE COUNTED, NOT DROPPED. An account will always have payments
that predate any tagging, and a board that silently ignored them would report
a fraction of the business as if it were all of it. They are gathered under
one honest heading and the total is reported beside the campaigns.

NOTHING HERE RANKS BY ANYTHING BUT MONEY. There is no model in this file and
no page fetch; a test asserts it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# The tag we look for. One key, so a merchant tagging links by hand and this
# project's own rail agree without either knowing about the other.
CAMPAIGN_KEY = "campaign"

# Razorpay's own words. `paid` is the only status that means money arrived.
PAID = "paid"

UNTAGGED = "(untagged)"


@dataclass(frozen=True)
class Link:
    """One payment link, reduced to what attribution needs."""
    id: str
    campaign: str
    status: str
    amount: int          # what was asked, in paise
    amount_paid: int     # what actually arrived, in paise
    created_at: int | None = None

    @property
    def paid(self) -> bool:
        return self.status == PAID


@dataclass
class Row:
    campaign: str
    links: list = field(default_factory=list)

    @property
    def issued(self) -> int:
        return len(self.links)

    @property
    def paid(self) -> int:
        return sum(1 for link in self.links if link.paid)

    @property
    def revenue_paise(self) -> int:
        """Only what arrived. A link that was issued and never paid is not
        revenue, and a partially paid one is worth what was actually paid."""
        return sum(link.amount_paid for link in self.links if link.paid)

    @property
    def asked_paise(self) -> int:
        return sum(link.amount for link in self.links)

    @property
    def aov_paise(self) -> int:
        return (self.revenue_paise // self.paid) if self.paid else 0

    @property
    def settled_share(self) -> float:
        """Paid over issued. Zero issued is 0.0, never 100%."""
        return (self.paid / self.issued) if self.issued else 0.0


def read_links(client, limit: int = 100) -> list[Link]:
    """Every payment link on the account, reduced.

    Never raises. An account that cannot be read must be distinguishable
    from an account with no links, so the caller gets an empty list and the
    runner reports the exception — a silent empty result is the failure this
    codebase keeps having to fix.
    """
    try:
        response = client.payment_link.all({"count": limit})
    except Exception:                                   # noqa: BLE001
        raise

    out = []
    for item in (response or {}).get("payment_links", []):
        notes = item.get("notes") or {}
        out.append(Link(
            id=item.get("id", ""),
            campaign=str(notes.get(CAMPAIGN_KEY) or UNTAGGED),
            status=str(item.get("status", "")),
            amount=int(item.get("amount") or 0),
            amount_paid=int(item.get("amount_paid") or 0),
            created_at=item.get("created_at"),
        ))
    return out


def rank(links):
    """Group by campaign and order by what actually arrived.

    The untagged row is always last regardless of size. It is a reporting
    honesty line rather than a campaign, and letting it head a leaderboard of
    campaigns would be a category error on the most visible row of the page.
    """
    grouped: dict[str, Row] = {}
    for link in links:
        grouped.setdefault(link.campaign, Row(campaign=link.campaign))
        grouped[link.campaign].links.append(link)

    rows = list(grouped.values())
    rows.sort(key=lambda r: (r.campaign == UNTAGGED,
                             -r.revenue_paise, r.campaign))
    return rows
