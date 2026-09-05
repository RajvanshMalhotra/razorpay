"""Signing up, and the price list a person actually types.

The parsing is arithmetic on purpose. A merchant typing its own stock should
get back exactly what it typed — a model that misreads a quantity would have
this business's own catalogue wrong on the first screen it ever sees.
"""
from scripts.serve import _price_lines, _slug


# --- the price list ----------------------------------------------------------

def test_the_last_two_numbers_are_the_quantity_and_the_price():
    """Read from the right, because a price list ends with its figures
    whatever the goods are called."""
    assert _price_lines("cold brew concentrate, 500 units, 210") == [
        {"title": "cold brew concentrate", "qty": 500, "price_paise": 21000}]


def test_a_name_that_starts_with_a_number_keeps_it():
    """Splitting on separators ate the 18650, which is the product."""
    assert _price_lines("18650 lithium cells, 200, 21")[0]["title"] == (
        "18650 lithium cells")


def test_a_quantity_does_not_end_up_in_the_name():
    """"paper cups 9000 x 12" was listing a product called "paper cups 9000"."""
    row = _price_lines("paper cups 9000 x 12")[0]

    assert row["title"] == "paper cups"
    assert row["qty"] == 9000 and row["price_paise"] == 1200


def test_pipes_commas_and_spaces_all_work():
    """Whatever they already write their list in."""
    rows = _price_lines("oat milk cartons | 300 | 95\n"
                        "vanilla syrup, 40, 260\n"
                        "jute twine 900 x 4")

    assert [r["title"] for r in rows] == [
        "oat milk cartons", "vanilla syrup", "jute twine"]


def test_one_number_is_a_price_not_a_quantity():
    """"vanilla syrup 150" is a price. Nobody writes a quantity alone."""
    row = _price_lines("vanilla syrup 150")[0]

    assert row["price_paise"] == 15000
    assert row["qty"] == 100


def test_a_line_with_no_price_is_skipped_not_invented():
    """A listing without a price is not a listing, and a made-up price is
    worse than a missing line."""
    assert _price_lines("things we sell") == []


def test_blank_lines_are_ignored():
    assert len(_price_lines("\n\ncold brew, 10, 20\n\n")) == 1


# --- the name ----------------------------------------------------------------

def test_a_business_name_becomes_an_actor_id():
    assert _slug("Bean & Barrel Coffee") == "m_bean_barrel_coffee"


def test_a_name_with_no_letters_gets_no_id():
    """Rejected up front rather than registering `m_` and failing later."""
    assert _slug("!!!") == ""
    assert _slug("") == ""
