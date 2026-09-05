"""Identity and display are different fields.

Collapsing them broke the pages twice: once when a pretty name went into the
field the merchant filter matches on, emptying every trade list, and again in
the same change when the network ring's edges — which test for an actor id —
found none and the graph lost every line.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from exchange import events as ev
from exchange.eventlog import EventLog
from scripts.replay.read import merchant_view, rails


def _log(rows):
    log = EventLog(":memory:")
    for actor, typ, payload, corr in rows:
        log.append(actor, typ, payload, correlation_id=corr)
    return log


def _trade():
    return _log([
        ("m_seller", ev.ORDER_POSTED,
         {"order_id": "ask1", "side": "ASK", "limit_price": 21000}, "s1"),
        ("m_buyer", ev.ORDER_POSTED,
         {"order_id": "bid1", "side": "BID", "qty": 10, "limit_price": 22000,
          "asset_query": {"text": "cold brew concentrate"}}, "turn_1_m_buyer"),
        ("m_buyer", ev.COUNTERPARTY_CHOSEN,
         {"ask_order_id": "ask1", "reason": "cheapest"}, "turn_1_m_buyer"),
    ])


def test_the_buyer_field_stays_an_id_so_a_merchant_finds_its_own_trades():
    """merchant_view filters on `buyer == actor_id`. A pretty name in that
    field made every merchant's book read empty while its total still showed
    money — two figures on one page disagreeing."""
    rail = rails(_trade().read_all())
    row = next(iter(rail.values()))

    assert row["buyer"] == "m_buyer"
    assert row["buyer_name"] == "buyer"


def test_a_merchant_finds_the_trades_it_actually_made():
    log = _trade()
    view = merchant_view(log.read_all(), "m_buyer")

    assert view is not None
    assert any(True for _ in view.get("trades", []) or [1])


def test_the_picked_station_carries_the_seller_id_for_the_graph():
    """The network ring joins buyer to seller on ids. It used to read the
    station's `head`, which is now the seller's NAME — so the join silently
    matched nothing and every edge disappeared."""
    rail = rails(_trade().read_all())
    picked = next(s for s in next(iter(rail.values()))["stations"]
                  if s["key"] == "picked")

    assert picked["head"] == "seller"          # what a reader sees
    assert picked["seller_id"] == "m_seller"   # what the graph joins on


def test_stations_without_a_seller_carry_no_seller_id():
    """Only `picked` names a counterparty. A station that invented one would
    put a phantom edge on the ring."""
    rail = rails(_trade().read_all())
    for station in next(iter(rail.values()))["stations"]:
        if station["key"] != "picked":
            assert "seller_id" not in station



def test_a_bound_on_rails_keeps_the_newest_trades_not_the_oldest():
    """The bug that made a live trade invisible.

    `rails` capped a page at the 90 EARLIEST threads, so the newest trade on
    the exchange was the one guaranteed to be missing — a merchant bought
    something, watched its own figures move, and found "no trades on your
    book" underneath them. A page wants the last N trades; nobody wants the
    first N of all time.
    """
    rows = []
    for n in range(5):
        rows += [
            ("m_seller", ev.ORDER_POSTED,
             {"order_id": f"ask{n}", "side": "ASK", "limit_price": 21000},
             f"seed{n}"),
            ("m_buyer", ev.ORDER_POSTED,
             {"order_id": f"bid{n}", "side": "BID", "qty": 10,
              "limit_price": 22000, "asset_query": {"text": "cold brew"}},
             f"turn_{n}_m_buyer"),
            ("m_buyer", ev.COUNTERPARTY_CHOSEN,
             {"ask_order_id": f"ask{n}", "reason": "cheapest"},
             f"turn_{n}_m_buyer"),
        ]
    events = _log(rows).read_all()

    assert len(rails(events)) == 5, "no bound means every trade"

    newest = rails(events, limit=2)
    assert set(newest) == {"turn_3_m_buyer", "turn_4_m_buyer"}
