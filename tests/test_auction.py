import pytest

from exchange.eventlog import EventLog
from exchange.house.auction import Bid, clear, run_auction


@pytest.fixture
def log(tmp_path):
    lg = EventLog(str(tmp_path / "auction.db"))
    yield lg
    lg.close()


def test_the_highest_bidder_wins():
    result = clear([Bid("m_a", 800, ""), Bid("m_b", 1850, ""), Bid("m_c", 1200, "")])

    assert result.winner_id == "m_b"


def test_the_winner_pays_the_second_price():
    """The whole point: your bid decides whether you win, not what you pay."""
    result = clear([Bid("m_a", 800, ""), Bid("m_b", 1850, ""), Bid("m_c", 1200, "")])

    assert result.price == 1200


def test_a_single_bid_does_not_clear():
    """A market of one has no price."""
    result = clear([Bid("m_a", 800, "")])

    assert result.winner_id is None
    assert "one bid" in result.reason


def test_no_bids_does_not_clear():
    result = clear([])

    assert result.winner_id is None


def test_a_tie_at_the_top_clears_at_that_price():
    result = clear([Bid("m_a", 1000, ""), Bid("m_b", 1000, "")])

    assert result.price == 1000
    assert result.winner_id in {"m_a", "m_b"}


def test_running_an_auction_logs_open_bids_and_clearing(log):
    run_auction(log, "ins_1",
                [Bid("m_a", 800, "small category for us"),
                 Bid("m_b", 1850, "we spend 40k a month here")],
                correlation_id="c1")

    types = [e.type for e in log.read_by_correlation("c1")]
    assert types == ["AUCTION_OPENED", "BID_PLACED", "BID_PLACED", "AUCTION_CLEARED"]


def test_each_bid_records_the_reasoning_behind_it(log):
    """Under second-price, honest valuation is optimal — so the reasoning is
    about worth, and that is what belongs in the trail."""
    run_auction(log, "ins_1",
                [Bid("m_a", 800, "small category for us"),
                 Bid("m_b", 1850, "we spend 40k a month here")],
                correlation_id="c1")

    placed = [e for e in log.read_by_correlation("c1") if e.type == "BID_PLACED"]
    assert "40k a month" in placed[1].payload["reason"]


def test_the_clearing_event_records_winner_and_price(log):
    run_auction(log, "ins_1",
                [Bid("m_a", 800, ""), Bid("m_b", 1850, ""), Bid("m_c", 1200, "")],
                correlation_id="c1")

    cleared = [e for e in log.read_by_correlation("c1")
               if e.type == "AUCTION_CLEARED"][0]
    assert cleared.payload["winner_id"] == "m_b"
    assert cleared.payload["price"] == 1200


def test_a_no_clear_is_still_logged(log):
    """Not clearing is an outcome, not an error."""
    run_auction(log, "ins_1", [Bid("m_a", 800, "")], correlation_id="c1")

    cleared = [e for e in log.read_by_correlation("c1")
               if e.type == "AUCTION_CLEARED"][0]
    assert cleared.payload["winner_id"] is None
