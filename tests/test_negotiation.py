from exchange.agents.negotiation import (
    Offer,
    gap_stalled,
    negotiate,
    parse_offer,
)
from exchange.llm.scripted import ScriptedProvider


def test_parse_offer_reads_a_price():
    price, walk = parse_offer("I can do PRICE: 1940 on those terms.")

    assert price == 1940
    assert walk is False


def test_parse_offer_detects_walking_away():
    price, walk = parse_offer("WALK: we are too far apart on delivery.")

    assert walk is True


def test_parse_offer_returns_none_when_there_is_no_price():
    price, walk = parse_offer("Tell me more about the volumes first.")

    assert price is None
    assert walk is False


def test_gap_stalled_is_false_while_the_sides_are_closing():
    offers = [
        Offer("buyer", 1800, ""), Offer("seller", 2200, ""),
        Offer("buyer", 1900, ""), Offer("seller", 2000, ""),
    ]

    assert gap_stalled(offers) is False


def test_gap_stalled_is_true_when_the_gap_stops_moving():
    offers = [
        Offer("buyer", 1900, ""), Offer("seller", 2000, ""),
        Offer("buyer", 1901, ""), Offer("seller", 1999, ""),
        Offer("buyer", 1902, ""), Offer("seller", 1998, ""),
    ]

    assert gap_stalled(offers, epsilon=100) is True


def test_gap_stalled_sees_through_oscillation():
    """Each offer moves a lot; the gap does not. Movement is not progress."""
    offers = [
        Offer("buyer", 1900, ""), Offer("seller", 2000, ""),
        Offer("buyer", 2000, ""), Offer("seller", 1900, ""),
        Offer("buyer", 1900, ""), Offer("seller", 2000, ""),
    ]

    assert gap_stalled(offers, epsilon=50) is True


def test_negotiation_agrees_when_the_seller_accepts():
    buyer = ScriptedProvider(["PRICE: 1900 — that is my offer."])
    seller = ScriptedProvider(["PRICE: 1900 — agreed."])

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2000, buyer_limit=2200, seller_floor=1800)

    assert outcome.agreed is True
    assert outcome.final_price == 1900
    assert outcome.ended_reason == "agreed"


def test_an_agent_can_walk_away_and_the_reason_is_kept():
    buyer = ScriptedProvider(["WALK: the gap is not worth another round."])
    seller = ScriptedProvider(["PRICE: 2100"])

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2100, buyer_limit=2200, seller_floor=1800)

    assert outcome.agreed is False
    assert "walked" in outcome.ended_reason
    assert "not worth another round" in outcome.offers[-1].message


def test_a_stalled_negotiation_ends_without_agreement():
    buyer = ScriptedProvider(["PRICE: 1900", "PRICE: 1901", "PRICE: 1902", "PRICE: 1903"])
    seller = ScriptedProvider(["PRICE: 2000", "PRICE: 1999", "PRICE: 1998", "PRICE: 1997"])

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2000, buyer_limit=2200, seller_floor=1800)

    assert outcome.agreed is False
    assert outcome.ended_reason == "stalled"


def test_the_token_budget_backstops_a_runaway():
    """Should never fire in a healthy run. If it does, it is a bug upstream."""
    buyer = ScriptedProvider(["PRICE: 1900"] * 50)
    seller = ScriptedProvider(["PRICE: 2100"] * 50)

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2000, buyer_limit=2200, seller_floor=1800,
                        token_budget=200)

    assert outcome.agreed is False
    assert outcome.ended_reason == "token budget exhausted"
