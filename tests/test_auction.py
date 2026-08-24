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


# --- an unreadable reply is not a bid of zero -------------------------------


def test_an_unreadable_reply_cannot_set_a_price():
    """Two scouts that fail to parse used to let the third win at price 0,
    with AUCTION_CLEARED recording 'cleared at the second price'."""
    result = clear([Bid("m_a", 0, "I am not sure", parsed=False),
                    Bid("m_b", 0, "hard to say", parsed=False),
                    Bid("m_c", 1200, "BID: 1200 worth a look")])

    assert result.winner_id is None
    assert result.price is None
    assert "could not be read" in result.reason


def test_an_unreadable_reply_is_excluded_from_the_ranking_not_the_log(log):
    run_auction(log, "ins_1",
                [Bid("m_a", 0, "I am not sure", parsed=False),
                 Bid("m_b", 1850, "BID: 1850"),
                 Bid("m_c", 1200, "BID: 1200")],
                correlation_id="c1")

    placed = [e for e in log.read_by_correlation("c1") if e.type == "BID_PLACED"]
    cleared = [e for e in log.read_by_correlation("c1")
               if e.type == "AUCTION_CLEARED"][0]

    assert len(placed) == 3, "the unusable reply still belongs in the trail"
    assert placed[0].payload["parsed"] is False
    # ...and the price came from the two readable bids only.
    assert cleared.payload["winner_id"] == "m_b"
    assert cleared.payload["price"] == 1200


def test_a_genuine_zero_is_still_a_bid():
    """Valuing a lot at nothing is an opinion about worth. Only an unreadable
    reply is an absent bid."""
    result = clear([Bid("m_a", 0, "BID: 0 worthless to us"),
                    Bid("m_b", 1200, "BID: 1200")])

    assert result.winner_id == "m_b"
    assert result.price == 0


def test_an_empty_auction_does_not_describe_a_market_of_one():
    """The reason lands verbatim in AUCTION_CLEARED. There is no market of
    one here; there is no market."""
    assert clear([]).reason == "no bids; there is no market"


# --- the purchase goes through the gate ------------------------------------


@pytest.fixture
def exchange(log):
    from exchange.rails.credits import CreditRail
    from exchange.service import Exchange

    return Exchange(log, index=None, inr_rail=None, credit_rail=CreditRail(log))


def _seeded(log, actor_id, points):
    from exchange.house.accountant import Accountant

    Accountant(log, None).mint(actor_id, points, None, correlation_id="seed",
                               reason="opening balance")


def _auction(log, corr="c1"):
    return run_auction(log, "ins_1",
                       [Bid("m_a", 800, "BID: 800"),
                        Bid("m_b", 1850, "BID: 1850"),
                        Bid("m_c", 1200, "BID: 1200")],
                       correlation_id=corr)


def test_a_policy_decision_precedes_every_point_that_moves(exchange, log):
    """The auction used to pay out with a raw log.append: no POLICY_DECIDED,
    no balance check, no SETTLEMENT_INITIATED for the auditor to join on. The
    one flow built to showcase the gate was the only one that never fired it."""
    from exchange.house.auction import settle_purchase

    _seeded(log, "m_b", 1850)
    result = _auction(log)

    decision, settlement = settle_purchase(exchange, "ins_1", result,
                                           correlation_id="c1")

    assert decision.verdict.value == "ALLOW"
    assert settlement.amount == 1200

    events = log.read_by_correlation("c1")
    transferred = [e for e in events if e.type == "CREDITS_TRANSFERRED"]
    assert transferred, "the purchase must actually move points"
    for moved in transferred:
        initiated = [e for e in events
                     if e.type == "SETTLEMENT_INITIATED"
                     and e.payload["settlement_id"] == moved.payload["settlement_id"]][0]
        allow = [e for e in events
                 if e.type == "POLICY_DECIDED"
                 and e.payload["action_ref"] == initiated.payload["match_id"]
                 and e.payload["verdict"] == "ALLOW"][0]
        assert allow.seq < moved.seq, "the gate fires before the money moves"


def test_the_purchase_is_inside_the_accountants_invariants(exchange, log):
    from exchange.house.accountant import Accountant
    from exchange.house.auction import settle_purchase

    _seeded(log, "m_b", 1850)
    settle_purchase(exchange, "ins_1", _auction(log), correlation_id="c1")

    assert Accountant(log, None).assert_invariants() == []


def test_buying_an_insight_does_not_itself_mint(exchange, log):
    """Points are earned by trading goods well. Minting on a points purchase
    would pay merchants for spending points."""
    from exchange.house.auction import settle_purchase

    _seeded(log, "m_b", 1850)
    settle_purchase(exchange, "ins_1", _auction(log), correlation_id="c1")

    types = [e.type for e in log.read_by_correlation("c1")]
    assert "POINTS_MINTED" not in types


def test_a_winner_who_cannot_pay_is_refused_not_driven_negative(exchange, log):
    """The bug pinned the blame backwards: with no balance check, a clearing
    price above the winner's balance drove it negative and surfaced as a
    points_not_conserved violation against the merchant — the auction blaming
    the buyer for the house's missing check."""
    from exchange.house.accountant import Accountant
    from exchange.house.auction import settle_purchase
    from exchange.projections import fold
    from exchange.rails.base import InsufficientCredits

    _seeded(log, "m_b", 100)
    result = _auction(log)

    with pytest.raises(InsufficientCredits):
        settle_purchase(exchange, "ins_1", result, correlation_id="c1")

    types = [e.type for e in log.read_by_correlation("c1")]
    assert "SETTLEMENT_FAILED" in types
    assert "CREDITS_TRANSFERRED" not in types
    assert fold(log.read_all()).credit_balances["m_b"] == 100

    violations = Accountant(log, None).assert_invariants()
    assert not any(v.kind == "points_not_conserved" for v in violations)


def test_an_auction_that_did_not_clear_settles_nothing(exchange, log):
    from exchange.house.auction import Clearing, settle_purchase

    decision, settlement = settle_purchase(
        exchange, "ins_1", Clearing(None, None, "no bids; there is no market"),
        correlation_id="c1",
    )

    assert (decision, settlement) == (None, None)
    assert log.read_by_correlation("c1") == []


def test_royalties_come_out_of_what_the_house_took_in(exchange, log):
    """The house holds a real balance now, funded by what it sells — and the
    payout is bounded by the clearing price that funded it."""
    from exchange.house.accountant import Accountant
    from exchange.house.auction import pay_royalties, settle_purchase
    from exchange.house.points import royalty_for
    from exchange.projections import fold

    _seeded(log, "m_b", 1850)
    result = _auction(log)
    settle_purchase(exchange, "ins_1", result, correlation_id="c1")

    per, paid = pay_royalties(exchange, "ins_1", ["m_x", "m_y", "m_z"],
                              result.price, correlation_id="c1")

    assert per == royalty_for(1200, 3)
    assert paid == 3
    balances = fold(log.read_all()).credit_balances
    assert balances["m_x"] == per
    assert balances["house"] == 1200 - per * 3 > 0, "the residual stays with the house"
    assert Accountant(log, None).assert_invariants() == []


def test_every_royalty_is_gated_too(exchange, log):
    """A point leaving the house's balance is a money action like any other."""
    from exchange.house.auction import pay_royalties, settle_purchase

    _seeded(log, "m_b", 1850)
    settle_purchase(exchange, "ins_1", _auction(log), correlation_id="c1")
    pay_royalties(exchange, "ins_1", ["m_x", "m_y", "m_z"], 1200, correlation_id="c1")

    events = log.read_by_correlation("c1")
    moves = [e for e in events if e.type == "CREDITS_TRANSFERRED"]
    decided = {e.payload["action_ref"] for e in events
               if e.type == "POLICY_DECIDED" and e.payload["verdict"] == "ALLOW"}
    assert len(moves) == 4, "one purchase and three royalties"
    for moved in moves:
        initiated = [e for e in events
                     if e.type == "SETTLEMENT_INITIATED"
                     and e.payload["settlement_id"] == moved.payload["settlement_id"]][0]
        assert initiated.payload["match_id"] in decided


def test_the_house_cannot_distribute_points_it_never_received(exchange, log):
    """Without a funded balance the house was an infinite source. The rail
    refuses, and the distribution stops rather than going negative."""
    from exchange.house.accountant import Accountant
    from exchange.house.auction import pay_royalties
    from exchange.projections import fold

    per, paid = pay_royalties(exchange, "ins_1", ["m_x", "m_y", "m_z"], 1200,
                              correlation_id="c1")

    assert per > 0
    assert paid == 0
    assert fold(log.read_all()).credit_balances.get("house", 0) == 0
    assert not any(v.kind == "points_not_conserved"
                   for v in Accountant(log, None).assert_invariants())
