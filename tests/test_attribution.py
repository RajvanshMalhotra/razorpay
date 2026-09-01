"""Campaign to cash, read from a real Razorpay account."""
import inspect

import pytest

from exchange.house.attribution import (
    CAMPAIGN_KEY,
    UNTAGGED,
    Link,
    rank,
    read_links,
)


class Account:
    """Stands in for the Razorpay client's payment_link resource."""

    def __init__(self, links, error=None):
        self._links, self._error = links, error
        self.payment_link = self

    def all(self, params=None):
        if self._error:
            raise self._error
        return {"payment_links": self._links}


def _link(campaign=None, status="paid", amount=100_000, paid=None, lid="pl"):
    notes = {"match_id": "m"}
    if campaign:
        notes[CAMPAIGN_KEY] = campaign
    return {"id": lid, "notes": notes, "status": status, "amount": amount,
            "amount_paid": amount if paid is None else paid}


def test_nothing_here_calls_a_model_or_fetches_a_page():
    source = inspect.getsource(rank)
    start = source.index('"""')
    body = source[:start] + source[source.index('"""', start + 3) + 3:]
    assert "provider" not in body and "urllib" not in body


def test_the_tag_that_travelled_with_the_money_is_what_groups_it():
    links = read_links(Account([
        _link("diwali_instagram", lid="pl1"),
        _link("diwali_instagram", lid="pl2"),
        _link("summer_meta", lid="pl3"),
    ]))

    rows = rank(links)

    assert [r.campaign for r in rows] == ["diwali_instagram", "summer_meta"]
    assert rows[0].issued == 2 and rows[0].paid == 2


def test_only_a_paid_link_is_revenue():
    """Razorpay's own status. An issued link that nobody paid is not money,
    and a board that counted it would report revenue that does not exist."""
    links = read_links(Account([
        _link("a", status="paid", amount=50_000),
        _link("a", status="created", amount=90_000, paid=0),
    ]))

    row = rank(links)[0]

    assert row.issued == 2 and row.paid == 1
    assert row.revenue_paise == 50_000
    assert row.asked_paise == 140_000        # what was hoped for, separately
    assert row.settled_share == 0.5


def test_a_partly_paid_link_is_worth_what_was_actually_paid():
    """`amount` and `amount_paid` both exist and only one of them is real."""
    links = read_links(Account([
        _link("a", status="paid", amount=100_000, paid=40_000)]))

    assert rank(links)[0].revenue_paise == 40_000


def test_untagged_links_are_counted_and_never_lead_the_board():
    """Every real account has payments predating any tagging. Ignoring them
    reports a fraction of the business as if it were all of it — and letting
    them head a leaderboard of campaigns is a category error on the most
    visible row of the page."""
    links = read_links(Account([
        _link(None, amount=900_000, lid="pl1"),
        _link(None, amount=900_000, lid="pl2"),
        _link("small_campaign", amount=1_000, lid="pl3"),
    ]))

    rows = rank(links)

    assert [r.campaign for r in rows] == ["small_campaign", UNTAGGED]
    assert rows[1].revenue_paise == 1_800_000     # counted, just not first


def test_a_campaign_nobody_was_asked_to_pay_for_settled_nothing():
    rows = rank([Link("pl", "a", "created", 5000, 0)])

    assert rows[0].settled_share == 0.0
    assert rows[0].aov_paise == 0


def test_an_unreadable_account_raises_rather_than_reporting_no_links():
    """An account that cannot be read must be distinguishable from an account
    with no links. A silent empty result is the failure this codebase keeps
    having to fix."""
    with pytest.raises(RuntimeError):
        read_links(Account([], error=RuntimeError("bad key")))
