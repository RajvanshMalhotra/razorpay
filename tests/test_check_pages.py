"""The check that guards every page — and the day it was guarding nothing.

The scanner reads two things: the text a page renders as HTML, and the text a
page renders at runtime from the JSON embedded in it. The second half stopped
at the root of that JSON for as long as it existed, so it reported every page
clean while actor ids and paise sat one level down in the strings the page
prints. These tests are about that half.
"""
import json

from scripts.replay.check_pages import PATTERNS, payloads, scan, visible


def _page(payload) -> str:
    return ('<html><body><p>a page</p>'
            '<script type="application/json" id="mkt">'
            + json.dumps(payload) + '</script></body></html>')


def test_the_scan_reaches_text_nested_under_keys_of_its_own():
    """The regression. The root of the payload is keyed "rails", not "said",
    and gating the WALK on the key name instead of the collection meant the
    walk never started."""
    page = _page({"rails": {"turn_1": {"stations": [
        {"key": "picked", "lines": ["Seller m_reelco at 5000 per unit"]}]}}})

    assert "m_reelco" in payloads(page)


def test_identity_fields_are_not_read_as_prose():
    """An id is meant to look like an id where the page joins on it. Only
    what a reader sees is held to the reader's standard."""
    page = _page({"rails": {"t": {"stations": [
        {"head": "reelco", "seller_id": "m_reelco", "corr": "turn_1_m_a"}]}}})
    text = payloads(page)

    assert "reelco" in text
    assert "m_reelco" not in text
    assert "turn_1_m_a" not in text


def test_a_page_that_prints_paise_from_its_json_fails(tmp_path):
    page = tmp_path / "m-x.html"
    page.write_text(_page({"rails": {"t": {"talk": [
        {"said": "PRICE: 24500 we accept"}]}}}))

    found = scan([str(page)])

    assert "bare big number" in found
    assert found["bare big number"][str(page)] == ["24500"]


def test_a_clean_page_passes(tmp_path):
    page = tmp_path / "m-x.html"
    page.write_text(_page({"rails": {"t": {"talk": [
        {"who": "m_a", "who_name": "reelco", "said": "we accept at ₹245"}]}}}))

    assert scan([str(page)]) == {}


def test_the_html_a_reader_sees_is_scanned_too():
    """Both halves. The JSON half is the one that broke, and a fix that
    quietly dropped the other half would pass the tests above."""
    text = visible('<html><body><p>m_reelco owes 3120000</p>'
                   '<style>.x{color:red}</style></body></html>')

    assert "m_reelco" in text and "3120000" in text
    assert "color:red" not in text


def test_every_pattern_is_a_thing_a_merchant_should_not_read():
    assert set(PATTERNS) == {"actor id", "correlation id", "settlement id",
                             "order/match id", "the word paise",
                             "bare big number"}
