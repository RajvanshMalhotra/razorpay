import pytest

from exchange.eventlog import EventLog
from exchange.events import SETTLEMENT_COMPLETED, SETTLEMENT_INITIATED
from exchange.house.accountant import Accountant
from exchange.house.agent import HouseAgent
from exchange.house.auction import run_auction
from exchange.house.points import points_for_settlement, royalty_for
from exchange.llm.scripted import ScriptedProvider
from tests.test_rails import FakeRazorpay

CORR = "corr_intel"


@pytest.fixture
def log(tmp_path):
    lg = EventLog(str(tmp_path / "intel.db"))
    for i in range(30):
        lg.append(f"m_{i}", SETTLEMENT_INITIATED,
                  {"settlement_id": f"s{i}", "match_id": f"m{i}", "currency": "INR",
                   "amount": 380_000, "razorpay_order_id": f"order_{i}"},
                  correlation_id=f"c{i}")
        lg.append(f"m_{i}", SETTLEMENT_COMPLETED,
                  {"settlement_id": f"s{i}", "razorpay_payment_id": f"pay_{i}"},
                  correlation_id=f"c{i}")
    yield lg
    lg.close()


def test_a_lot_is_minted_auctioned_and_cleared_on_one_correlation_id(log):
    from exchange.house.auction import Bid

    house = HouseAgent(log, ScriptedProvider(["skincare AOV up 12% week on week"]))
    lot = house.mint_from(house.observe(), CORR)
    assert lot is not None

    result = run_auction(log, lot.asset_id,
                         [Bid("m_1", 800, "small category for us"),
                          Bid("m_2", 1850, "we spend 40k a month here"),
                          Bid("m_3", 1200, "worth a look")],
                         correlation_id=CORR)

    assert result.winner_id == "m_2"
    assert result.price == 1200, "second price, not the winner's own bid"

    types = [e.type for e in log.read_by_correlation(CORR)]
    assert types[0] == "INSIGHT_MINTED"
    assert types[-1] == "AUCTION_CLEARED"


def test_the_free_headline_is_public_and_the_playbook_is_not(log):
    house = HouseAgent(log, ScriptedProvider(["skincare AOV up 12%"]))
    house.mint_from(house.observe(), CORR)

    assert house.feed() == ("skincare AOV up 12%",)
    minted = [e for e in log.read_by_correlation(CORR)
              if e.type == "INSIGHT_MINTED"][0]
    assert "playbook" not in minted.payload


def test_contributors_earn_when_their_win_is_bought(log):
    """What turns the product from extraction into a deal."""
    house = HouseAgent(log, ScriptedProvider(["skincare AOV up 12%"]))
    lot = house.mint_from(house.observe(), CORR)

    per_contributor = royalty_for(1200, len(lot.spec["contributor_ids"]))

    assert per_contributor > 0
    assert per_contributor * len(lot.spec["contributor_ids"]) <= 1200


def test_a_sharp_small_trade_out_earns_a_sloppy_large_one(log):
    sharp = points_for_settlement(190_000, ask_price=1940, qty=100, delivered=True)
    sloppy = points_for_settlement(1_940_000, ask_price=1940, qty=1000, delivered=True)

    assert sharp > sloppy


def test_the_accountant_finds_nothing_wrong_with_a_clean_run(log):
    client = FakeRazorpay(payments_by_order={
        f"order_{i}": {"count": 1, "items": [{"id": f"pay_{i}", "status": "captured"}]}
        for i in range(30)
    })

    accountant = Accountant(log, client)

    assert accountant.reconcile() == []
