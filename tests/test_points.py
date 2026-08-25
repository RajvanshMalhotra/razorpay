from exchange.house.points import (
    BASE_POINTS,
    EARNING_RATE_BPS,
    ROYALTY_SHARE_BPS,
    points_for_settlement,
    royalty_for,
)


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
    """The property is about the WHOLE distribution, not one share.

    `royalty_for(1200, 1) <= 1200` passed with a factor of three in hand and
    told us nothing: the hazard is n contributors each being paid, summing to
    more than the house took in. Floor division on basis points means the
    residual is kept rather than created, so the sum is bounded by the share
    and can never exceed the price — checked across sizes and splits, and at
    the odd numbers where the rounding actually lands.
    """
    for price in (0, 1, 7, 999, 1200, 2401, 1_000_000):
        for n in (1, 2, 3, 7, 30, 999):
            per = royalty_for(price, n)
            assert per >= 0
            assert per * n <= (price * ROYALTY_SHARE_BPS) // 10_000
            assert per * n <= price


def test_a_settlement_of_nothing_earns_nothing():
    """BASE_POINTS used to sit outside the margin bound, so it leaked.

    A settlement at zero consumed none of the spend cap and still counted as
    a completed trade, paying BASE_POINTS. Looping it minted points for free
    while the auditor found nothing to report.
    """
    assert points_for_settlement(
        amount=0, ask_price=1940, qty=500, delivered=True,
    ) == 0


def test_a_negative_amount_earns_nothing():
    assert points_for_settlement(
        amount=-100, ask_price=1940, qty=500, delivered=True,
    ) == 0


def test_a_real_trade_still_earns_its_base_and_its_margin():
    """The floor must not cost an honest trade anything.

    Asked 1940 x 500 = 970,000; paid 880,000; so the margin captured is
    90,000 and that — not the amount paid — is what earns.
    """
    margin = (1940 * 500) - 880_000
    assert points_for_settlement(
        amount=880_000, ask_price=1940, qty=500, delivered=True,
    ) == BASE_POINTS + ((margin * EARNING_RATE_BPS) // 10_000)
