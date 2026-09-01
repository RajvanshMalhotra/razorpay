import pytest

from exchange.eventlog import EventLog
from exchange.events import SETTLEMENT_COMPLETED, SETTLEMENT_INITIATED
from exchange.house.agent import HouseAgent
from exchange.llm.scripted import ScriptedProvider


@pytest.fixture
def log(tmp_path):
    lg = EventLog(str(tmp_path / "house.db"))
    yield lg
    lg.close()


def _settle(log, actor, amount, corr):
    log.append(actor, SETTLEMENT_INITIATED,
               {"settlement_id": f"stl_{corr}", "match_id": f"m_{corr}",
                "currency": "INR", "amount": amount}, correlation_id=corr)
    log.append(actor, SETTLEMENT_COMPLETED,
               {"settlement_id": f"stl_{corr}", "razorpay_payment_id": "pay"},
               correlation_id=corr)


def test_observe_reads_settled_activity_from_the_log(log):
    for i in range(3):
        _settle(log, f"m_{i}", 100_000 * (i + 1), f"c{i}")

    observations = HouseAgent(log, ScriptedProvider([])).observe()

    assert len(observations) == 3
    assert {o["actor_id"] for o in observations} == {"m_0", "m_1", "m_2"}


def test_observe_ignores_settlements_that_never_completed(log):
    """A PENDING settlement is not evidence of anything yet."""
    log.append("m_a", SETTLEMENT_INITIATED,
               {"settlement_id": "stl_1", "match_id": "m1",
                "currency": "INR", "amount": 500}, correlation_id="c")

    assert HouseAgent(log, ScriptedProvider([])).observe() == []


def test_minting_needs_enough_distinct_merchants(log):
    for i in range(30):
        _settle(log, f"m_{i}", 100_000, f"c{i}")
    house = HouseAgent(log, ScriptedProvider(["skincare demand is up 12% week on week"]))

    lot = house.mint_from(house.observe(), "c_house")

    assert lot is not None
    assert lot.spec["k"] == 30


def test_minting_refuses_below_the_floor_and_logs_it(log):
    for i in range(4):
        _settle(log, f"m_{i}", 100_000, f"c{i}")
    house = HouseAgent(log, ScriptedProvider(["a headline"]))

    lot = house.mint_from(house.observe(), "c_house")

    assert lot is None
    types = [e.type for e in log.read_by_correlation("c_house")]
    assert "PRIVACY_REFUSED" in types
    assert "INSIGHT_MINTED" not in types


def test_a_refusal_records_how_many_merchants_it_had(log):
    for i in range(4):
        _settle(log, f"m_{i}", 100_000, f"c{i}")
    house = HouseAgent(log, ScriptedProvider(["a headline"]))

    house.mint_from(house.observe(), "c_house")

    refused = [e for e in log.read_by_correlation("c_house")
               if e.type == "PRIVACY_REFUSED"][0]
    assert refused.payload["k"] == 4


def test_minting_writes_the_lot_to_the_log(log):
    for i in range(30):
        _settle(log, f"m_{i}", 100_000, f"c{i}")
    house = HouseAgent(log, ScriptedProvider(["skincare demand is up"]))

    house.mint_from(house.observe(), "c_house")

    minted = [e for e in log.read_by_correlation("c_house")
              if e.type == "INSIGHT_MINTED"][0]
    assert minted.payload["headline"] == "skincare demand is up"


def test_the_feed_carries_headlines_and_never_playbooks(log):
    """The free half creates the hunger; the auction sells the answer."""
    for i in range(30):
        _settle(log, f"m_{i}", 100_000, f"c{i}")
    house = HouseAgent(log, ScriptedProvider(["skincare demand is up"]))
    house.mint_from(house.observe(), "c_house")

    feed = house.feed()

    assert feed == ("skincare demand is up",)


def test_the_house_never_bids():
    """It mints, publishes and clears. A house that buys is not a market.

    `not hasattr(HouseAgent, "bid")` passed for any class that happens not to
    spell a method exactly that way — it would have let through a `place_bid`,
    a `buy`, or the house being handed to `run_auction` as a bidder. The
    property is about behaviour: the house has no way to express a valuation,
    and a bid carrying its id cannot survive an auction.
    """
    import inspect

    public = {n for n, _ in inspect.getmembers(HouseAgent, inspect.isfunction)
              if not n.startswith("_")}

    # Pinned as an exhaustive set rather than a blocklist of spellings. A
    # blocklist only catches the names someone thought of; this catches any
    # new capability at all, and whoever adds one has to come here and say
    # what it is.
    assert public == {"observe", "mint_from", "feed"}, (
        f"the house grew a capability: {public ^ {'observe', 'mint_from', 'feed'}}"
    )

    # Nor can it be handed to the auction as a bidder: producing a Bid is the
    # act of buying, and nothing here produces one.
    from exchange.house.auction import Bid

    sources = "".join(inspect.getsource(getattr(HouseAgent, n)) for n in public)
    assert Bid.__name__ not in sources


# --- the lot is the board's detail, not a trade count ------------------------

def test_a_lot_minted_from_the_board_carries_it_as_the_playbook():
    """The auction sells the detail behind the leaderboard. If the playbook is
    a trade count and a total, there is nothing a business would pay for and
    the auction is theatre."""
    log = EventLog(":memory:")
    house = HouseAgent(log, ScriptedProvider(["Several categories are climbing this week."]))
    board = [{"rank": 1, "campaign": "Cold Brew", "movement": 1.5,
              "merchants": 9, "value_paise": 620_000,
              "needs": ["cold brew concentrate"], "driver": "demand is up",
              "discussion": "Operators say margins are thin.",
              "threads": [{"title": "t", "subreddit": "r/x", "url": "u"}]}]

    lot = house.mint_from(
        [{"actor_id": f"m_{i}", "amount": 1000} for i in range(30)],
        correlation_id="c", board=board)

    assert lot is not None
    assert lot.spec["playbook"]["board"][0]["campaign"] == "Cold Brew"
    assert lot.spec["playbook"]["board"][0]["discussion"]
    assert lot.spec["playbook"]["board"][0]["threads"]
    assert lot.spec["category"] == "campaign_board"


def test_the_free_headline_does_not_give_away_the_paid_board():
    """The headline is published free. Naming the categories in it would move
    the paid half into the free half and leave nothing to auction."""
    seen = {}

    class Capture:
        def complete(self, messages, **kw):
            seen["asked"] = messages[0].content
            seen["system"] = kw.get("system", "")
            return type("R", (), {"text": "Something is moving."})()

    log = EventLog(":memory:")
    house = HouseAgent(log, Capture())
    house.mint_from([{"actor_id": f"m_{i}", "amount": 1} for i in range(30)],
                    correlation_id="c",
                    board=[{"rank": 1, "campaign": "Cold Brew", "movement": 1.5,
                            "merchants": 9, "value_paise": 1}])

    assert "Cold Brew" not in seen["asked"]
    assert "1.5x" in seen["asked"]
    assert "do not name" in seen["system"].lower()


def test_no_board_still_mints_the_old_way():
    """A run that has not published a board must still produce a lot."""
    log = EventLog(":memory:")
    house = HouseAgent(log, ScriptedProvider(["Micro-payments are spreading."]))

    lot = house.mint_from(
        [{"actor_id": f"m_{i}", "amount": 1000} for i in range(30)],
        correlation_id="c")

    assert lot is not None
    assert "board" not in lot.spec["playbook"]
    assert lot.spec["category"] == "market"
