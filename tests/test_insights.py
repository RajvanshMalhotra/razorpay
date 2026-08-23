import pytest

from exchange.house.insights import (
    K_MIN,
    check_privacy,
    mint_lot,
)
from exchange.models import AssetKind, Currency

MANY = tuple(f"m_{i}" for i in range(30))


def test_a_lot_from_enough_merchants_is_allowed():
    verdict = check_privacy(MANY)

    assert verdict.allowed is True
    assert verdict.k == 30


def test_a_lot_from_too_few_merchants_is_refused():
    verdict = check_privacy(tuple(f"m_{i}" for i in range(5)))

    assert verdict.allowed is False
    assert str(K_MIN) in verdict.reason


def test_a_single_merchant_lot_is_refused():
    """The case the whole floor exists to prevent."""
    verdict = check_privacy(("m_solo",))

    assert verdict.allowed is False


def test_duplicate_contributors_do_not_inflate_k():
    """Counting rows instead of merchants would let one merchant clear the floor."""
    verdict = check_privacy(("m_a",) * 40)

    assert verdict.k == 1
    assert verdict.allowed is False


def test_the_floor_is_exactly_k_min():
    assert check_privacy(tuple(f"m_{i}" for i in range(K_MIN))).allowed is True
    assert check_privacy(tuple(f"m_{i}" for i in range(K_MIN - 1))).allowed is False


def test_a_minted_lot_is_an_insight_priced_in_credits():
    lot = mint_lot("skincare AOV up 12%", {"channel": "meta"}, MANY, "skincare")

    assert lot.kind == AssetKind.INSIGHT
    assert lot.currency == Currency.CREDITS
    assert lot.origin_actor_id == "house"


def test_a_minted_lot_carries_its_headline_and_k():
    lot = mint_lot("skincare AOV up 12%", {"channel": "meta"}, MANY, "skincare")

    assert lot.spec["headline"] == "skincare AOV up 12%"
    assert lot.spec["k"] == 30
    assert lot.spec["category"] == "skincare"


def test_the_playbook_is_carried_but_is_not_the_headline():
    """The free half creates the demand; the auctioned half is the answer."""
    lot = mint_lot("conversion up 3.2x", {"channel": "meta", "spend": 40000}, MANY, "s")

    assert lot.spec["playbook"]["spend"] == 40000
    assert "spend" not in lot.spec["headline"]


def test_minting_below_the_floor_raises():
    with pytest.raises(ValueError, match="privacy"):
        mint_lot("a headline", {}, ("m_solo",), "skincare")
