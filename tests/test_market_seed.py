"""Seeding has one hard property: doing it twice must change nothing.

A two-hour run against a persistent log will be interrupted — this session has
already lost three agents to limits mid-task. Resumption is not a nicety, and
the log already knows who is registered and what is listed. Read it and skip.
"""
import pytest

from exchange.eventlog import EventLog
from exchange.models import Side
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex
from exchange.service import Exchange
from scripts.market.roster import MERCHANTS
from scripts.market.seed import seed
from tests.test_rails import FakeRazorpay
from tests.test_retrieval import fake_embedder


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "seed.db")


def _exchange(db):
    log = EventLog(db)
    return log, Exchange(
        log, HybridIndex(embed_fn=fake_embedder),
        RazorpayRail(log, FakeRazorpay()), CreditRail(log),
    )


def test_seeding_registers_every_merchant(db):
    log, exchange = _exchange(db)

    report = seed(exchange, MERCHANTS)

    assert report.registered == len(MERCHANTS)
    assert len(exchange.state().actors) == len(MERCHANTS)
    log.close()


def test_seeding_twice_registers_nobody_twice(db):
    log, exchange = _exchange(db)

    first = seed(exchange, MERCHANTS)
    second = seed(exchange, MERCHANTS)

    assert first.registered == len(MERCHANTS)
    assert second.registered == 0
    assert second.skipped_actors == len(MERCHANTS)
    assert len(exchange.state().actors) == len(MERCHANTS)
    log.close()


def test_seeding_twice_does_not_double_the_book(db):
    """A doubled book is not a cosmetic problem: every ask would have a twin
    at the same price, so `choose()` picks between duplicates of one seller."""
    log, exchange = _exchange(db)

    seed(exchange, MERCHANTS)
    first = len([o for o in exchange.state().open_orders.values()
                 if o.side == Side.ASK])
    seed(exchange, MERCHANTS)
    second = len([o for o in exchange.state().open_orders.values()
                  if o.side == Side.ASK])

    assert first > 0
    assert second == first
    log.close()


def test_seeding_twice_lists_no_asset_twice(db):
    log, exchange = _exchange(db)

    seed(exchange, MERCHANTS)
    first = len(exchange.state().assets)
    report = seed(exchange, MERCHANTS)

    assert len(exchange.state().assets) == first
    assert report.listed == 0
    assert report.skipped_assets == first
    log.close()


def test_a_fresh_exchange_over_a_seeded_log_can_still_match(db):
    """The index is rebuilt from the log, so a resumed run is not blind.

    Before this was fixed, a second process found zero candidates for asks
    that were plainly in the book — silently, which is the worst way for a
    two-hour run to fail.
    """
    from exchange.matching import find_candidates
    from exchange.models import Currency, Order

    log, exchange = _exchange(db)
    seed(exchange, MERCHANTS)
    log.close()

    log, resumed = _exchange(db)  # a different process, same database
    bid = Order(
        order_id="ord_probe", actor_id="m_probe", side=Side.BID,
        asset_ref=None, asset_query={"text": "cold brew concentrate"},
        qty=100, limit_price=40000, currency=Currency.INR,
        expires_at="2026-12-31T00:00:00+00:00", policy_snapshot={},
    )
    state = resumed.state()
    asks = [o for o in state.open_orders.values() if o.side == Side.ASK]

    assert find_candidates(bid, asks, state.assets, resumed.index)
    log.close()


def test_every_seeded_ask_belongs_to_the_merchant_that_sells_it(db):
    """An ask attributed to the wrong actor would let a merchant mint against
    a counterparty's listing, which the minter refuses — so it would silently
    produce trades that earn nothing."""
    log, exchange = _exchange(db)

    seed(exchange, MERCHANTS)

    by_asset = {listing.asset_id: m.actor_id
                for m in MERCHANTS for listing in m.sells}
    for order in exchange.state().open_orders.values():
        if order.side == Side.ASK:
            assert order.actor_id == by_asset[order.asset_ref], order
    log.close()


def test_seeding_prices_asks_in_paise_as_integers(db):
    log, exchange = _exchange(db)

    seed(exchange, MERCHANTS)

    for order in exchange.state().open_orders.values():
        assert isinstance(order.limit_price, int)
        assert order.limit_price > 0
    log.close()


def test_seeding_an_empty_roster_is_not_an_error(db):
    """A guard against the runner calling seed with a filtered list."""
    log, exchange = _exchange(db)

    report = seed(exchange, ())

    assert report.registered == 0
    assert report.listed == 0
    log.close()
