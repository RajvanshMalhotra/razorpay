"""The market read is sold, so the lock has to be real.

A page that renders the paid figures and hides them with a class is not a
paid product. It is a paid product's screenshot with a padlock drawn on it,
and one look in developer tools ends the argument. These tests are about the
numbers being ABSENT, not hidden.
"""
import pytest

from exchange import events as ev
from exchange.projections import fold
from scripts.replay.generate import _market_card


class E:
    def __init__(self, seq, actor, type_, payload, corr="c1"):
        self.seq, self.actor_id, self.type = seq, actor, type_
        self.payload, self.correlation_id = payload, corr
        self.ts = "2026-09-04T10:00:00Z"


BENCH = [
    {"rank": 1, "category": "Cold Brew Concentrate", "clears_paise": 19500,
     "ask_paise": 21000, "below_ask_share": 0.52, "median_saving": 0.10},
    {"rank": 2, "category": "Shipping Supplies", "clears_paise": 1700,
     "ask_paise": 1900, "below_ask_share": 0.0, "median_saving": 0.0},
]


def _register(actor="m_a", plan="standard"):
    return [E(1, actor, ev.ACTOR_REGISTERED,
              {"actor_id": actor, "kind": "MERCHANT", "merchant_id": None,
               "plan_tier": plan, "status": "ACTIVE"})]


# --- the lock ----------------------------------------------------------------

def test_a_standard_merchant_page_does_not_contain_the_paid_figures():
    html = _market_card({"plan": "standard"}, BENCH)

    for row in BENCH:
        assert row["category"] not in html
        assert str(row["clears_paise"] // 100) not in html
    assert "clears at" not in html.replace("category clears at", "")


def test_a_standard_merchant_is_told_a_gap_exists():
    """Free and deliberately useless alone. Knowing there is money on the
    table is what makes the detail worth buying; knowing neither is an ad."""
    html = _market_card({"plan": "standard"}, BENCH)

    assert "2 categories" in html
    assert "1 of them sellers are settling below their own ask" in html
    assert "Market plan" in html


def test_a_subscribed_merchant_gets_the_numbers():
    html = _market_card({"plan": "market"}, BENCH)

    assert "Cold Brew Concentrate" in html
    assert "₹195" in html and "₹210" in html
    assert "52% of trades close under the ask" in html


def test_a_category_that_never_moves_says_do_not_push():
    """The advice a benchmark carries is as valuable as the number. Pushing
    where sellers never move spends the counterparty's patience for nothing."""
    html = _market_card({"plan": "market"}, BENCH)

    assert "never move" in html


def test_no_card_at_all_before_any_benchmark_exists():
    assert _market_card({"plan": "market"}, []) == ""
    assert _market_card({"plan": "standard"}, None) == ""


# --- subscribing is a fact in the log ---------------------------------------

def test_a_plan_change_moves_the_tier():
    events = _register() + [
        E(2, "m_a", ev.PLAN_CHANGED, {"actor_id": "m_a", "plan_tier": "market"})]

    assert fold(events).actors["m_a"].plan_tier == "market"


def test_a_plan_change_does_not_touch_status():
    """Only the plan moves. A frozen merchant that subscribes is a frozen
    merchant on a better plan — the containment is not a billing question."""
    events = _register() + [
        E(2, "accountant", ev.ACTOR_FROZEN,
          {"actor_id": "m_a", "reason": "books disagree"}),
        E(3, "m_a", ev.PLAN_CHANGED, {"actor_id": "m_a", "plan_tier": "market"})]

    actor = fold(events).actors["m_a"]
    assert actor.plan_tier == "market"
    assert str(actor.status).endswith("FROZEN")


def test_a_plan_change_for_an_unknown_actor_is_ignored():
    events = [E(1, "m_ghost", ev.PLAN_CHANGED,
                {"actor_id": "m_ghost", "plan_tier": "market"})]

    assert "m_ghost" not in fold(events).actors


def test_the_page_reads_the_plan_as_it_stands_not_as_it_was():
    """A merchant that subscribes must not still be called standard by its
    own page."""
    from scripts.replay.read import _plan_now

    events = _register() + [
        E(2, "m_a", ev.PLAN_CHANGED, {"actor_id": "m_a", "plan_tier": "market"})]

    assert _plan_now(events, "m_a", events[0]) == "market"
    assert _plan_now(events, "m_b", None) == "standard"
