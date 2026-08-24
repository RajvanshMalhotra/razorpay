from exchange.house.points import points_for_settlement, royalty_for


def test_negotiating_below_the_ask_earns_more_than_paying_it():
    """Skill, not size: the whole point of the earning rule."""
    sharp = points_for_settlement(amount=380_000, ask_price=1940, qty=200, delivered=True)
    full = points_for_settlement(amount=388_000, ask_price=1940, qty=200, delivered=True)

    assert sharp > full


def test_a_small_sharp_trade_can_out_earn_a_large_sloppy_one():
    """Volume-weighting would make the biggest merchant win by round three."""
    small_sharp = points_for_settlement(190_000, ask_price=1940, qty=100, delivered=True)
    big_sloppy = points_for_settlement(1_940_000, ask_price=1940, qty=1000, delivered=True)

    assert small_sharp > big_sloppy


def test_paying_the_full_ask_still_earns_something():
    assert points_for_settlement(388_000, ask_price=1940, qty=200, delivered=True) > 0


def test_paying_above_the_ask_earns_nothing():
    """You cannot be paid for overpaying."""
    assert points_for_settlement(500_000, ask_price=1940, qty=200, delivered=True) == 0


def test_an_undelivered_trade_earns_nothing():
    assert points_for_settlement(380_000, ask_price=1940, qty=200, delivered=False) == 0


def test_points_are_whole_numbers():
    assert isinstance(points_for_settlement(380_001, 1940, 200, True), int)


def test_a_royalty_is_split_across_contributors():
    assert royalty_for(clearing_price=1200, contributor_count=30) < 1200


def test_a_royalty_scales_with_the_clearing_price():
    """A win that sells for more earns its contributors more."""
    assert royalty_for(2400, 30) > royalty_for(1200, 30)


def test_a_royalty_never_exceeds_the_clearing_price():
    assert royalty_for(1200, 1) <= 1200
