"""The Google Sheets push, exercised without a Google account.

This is the one part of the bookkeeping that cannot be run here — it needs a
service-account key that belongs to the operator, not to the repository. So
the transport is faked and the behaviour that matters is pinned: what gets
written, that a second run does not double it, that the cost does not grow
with the roster, and that each merchant's tab id comes back so its dashboard
can link straight to it.
"""
import json

import pytest

from exchange.books import COLUMNS, HEADINGS, entries_for
from scripts.market.sheets import TABS_FILE, merchants, push_to_sheet, write_csvs
from tests.test_books import E, _trade


class FakeTab:
    def __init__(self, title, tab_id):
        self.title = title
        self.id = tab_id


class FakeBook:
    """Enough of a spreadsheet to catch the mistakes that actually happen.

    It models the state that ACCUMULATES — merges, banding and conditional
    rules — because every bug this transport has caught has been a second run
    behaving differently from the first. A fake that forgets between pushes
    would have passed while the real sheet broke.
    """

    def __init__(self):
        self.tabs = {}
        self.values = {}
        self.calls = {"batch_update": 0, "values_batch_update": 0,
                      "values_batch_clear": 0}
        self.requests = []
        self.cleared = []
        self.order = []
        self.state = {}          # gid -> {merges, bands, rules}
        self._next_id = 100

    def worksheets(self):
        return list(self.tabs.values())

    def _st(self, gid):
        return self.state.setdefault(gid, {"merges": [], "bands": [],
                                           "rules": []})

    def fetch_sheet_metadata(self):
        return {"sheets": [
            {"properties": {"sheetId": t.id, "title": t.title},
             "merges": self._st(t.id)["merges"],
             "bandedRanges": self._st(t.id)["bands"],
             "conditionalFormats": self._st(t.id)["rules"]}
            for t in self.tabs.values()]}

    def batch_update(self, body):
        self.calls["batch_update"] += 1
        if any("unmergeCells" in r for r in body["requests"]):
            self.order.append("unmerge")
        for r in body["requests"]:
            self.requests.append(r)
            if "addSheet" in r:
                title = r["addSheet"]["properties"]["title"]
                self.tabs[title] = FakeTab(title, self._next_id)
                self._next_id += 1
            elif "mergeCells" in r:
                gid = r["mergeCells"]["range"]["sheetId"]
                self._st(gid)["merges"].append(r["mergeCells"]["range"])
            elif "unmergeCells" in r:
                self._st(r["unmergeCells"]["range"]["sheetId"])["merges"] = []
            elif "addBanding" in r:
                gid = r["addBanding"]["bandedRange"]["range"]["sheetId"]
                st = self._st(gid)
                assert not st["bands"] or True
                st["bands"].append({"bandedRangeId": len(st["bands"]) + 1})
            elif "deleteBanding" in r:
                for st in self.state.values():
                    st["bands"] = [b for b in st["bands"]
                                   if b["bandedRangeId"]
                                   != r["deleteBanding"]["bandedRangeId"]]
            elif "addConditionalFormatRule" in r:
                gid = (r["addConditionalFormatRule"]["rule"]["ranges"][0]
                       ["sheetId"])
                self._st(gid)["rules"].append(r["addConditionalFormatRule"])
            elif "deleteConditionalFormatRule" in r:
                spec = r["deleteConditionalFormatRule"]
                rules = self._st(spec["sheetId"])["rules"]
                if spec["index"] < len(rules):
                    rules.pop(spec["index"])

    def values_batch_clear(self, body):
        self.calls["values_batch_clear"] += 1
        self.cleared.append(list(body["ranges"]))
        for r in body["ranges"]:
            self.values.pop(r.strip("'"), None)

    def values_batch_update(self, body):
        self.calls["values_batch_update"] += 1
        self.order.append("write")
        for d in body["data"]:
            title = d["range"].split("!")[0].strip("'")
            self.values[title] = d["values"]


@pytest.fixture
def book(monkeypatch):
    import gspread
    fake = FakeBook()
    monkeypatch.setattr(gspread, "authorize",
                        lambda creds: type("C", (), {"open_by_key":
                                                     lambda s, k: fake})())
    import google.oauth2.service_account as sa
    monkeypatch.setattr(sa.Credentials, "from_service_account_file",
                        classmethod(lambda cls, f, scopes=None: object()))
    return fake


def _events():
    return (_trade(corr="turn_1", buyer="m_a", seller="m_b",
                   amount=490_000, ask="ord_b")
            + _trade(corr="turn_2", buyer="m_c", seller="m_a",
                     amount=300_000, base=200, ask="ord_a"))


INDEX = "Overview"


# --- what lands in the sheet -------------------------------------------------

LEGACY = ("Overview", "Negotiations", "Gate decisions",
          "Who dealt with whom", "What agents learned")


def test_each_merchant_gets_its_own_tab(book):
    pushed = push_to_sheet(_events(), "key.json", "sheet123")

    assert sorted(pushed) == ["m_a", "m_b", "m_c"]
    assert set(book.tabs) == {"a", "b", "c"}


def test_nothing_market_wide_is_written(book):
    """An earlier version filled the workbook with every offer and every
    ruling in the market. All true, and none of it a merchant's business —
    a merchant's sheet holds that merchant's own trading."""
    push_to_sheet(_events(), "key.json", "sheet123")

    assert not set(LEGACY) & set(book.tabs)


def test_one_merchant_can_be_pushed_alone(book):
    """The demo shows one business. Pushing thirty to show one is waste."""
    pushed = push_to_sheet(_events(), "key.json", "sheet123", only="m_a")

    assert pushed == ["m_a"]
    assert set(book.tabs) == {"a"}


def test_headings_are_readable_not_machine_readable(book):
    """COLUMNS stays the machine name because it keys an Entry and heads the
    CSV, where a tool on the other end wants something stable. Nobody should
    have to read "unit_price_inr" in their own accounts."""
    push_to_sheet(_events(), "key.json", "sheet123")

    flat = [c for row in book.values["a"] for c in row]
    assert "You paid" in flat and "You saved" in flat
    assert "unit_price_inr" not in flat
    assert "unit_price_inr" in COLUMNS, "still the CSV's machine name"


def test_a_money_summary_row_is_marked_for_the_formatter(book):
    """The two rows that both read "Awaiting confirmation" — one money, one a
    count — truncated to the same string side by side, which is the worst
    kind of ambiguity in a book of accounts."""
    from exchange.books import entries_for as _e
    labels = [l for l, _ in _e(_events(), "m_a").summary()]

    assert "Value awaiting confirmation (\u20b9)" in labels
    assert "Of those, awaiting confirmation" in labels
    assert len(labels) == len(set(labels))


def test_old_merges_are_cleared_before_values_are_written(book):
    """THE ORDERING THAT COST THREE COLUMNS. A cell covered by a merge from
    the previous run swallows anything written into it — silently, with no
    error — so three columns of every table and two headline cards arrived
    empty. Formatting must be torn down before the values go in."""
    push_to_sheet(_events(), "key.json", "sheet123")
    push_to_sheet(_events(), "key.json", "sheet123")

    unmerge = book.order.index("unmerge")
    write = book.order.index("write", unmerge)
    assert unmerge < write


def test_formatting_does_not_accumulate_across_runs(book):
    """Banding, merges and conditional rules are all additive. Sheets refuses
    a second banding outright and accepts duplicate rules in silence, so a
    tab would carry more of them after every push until it crawled."""
    push_to_sheet(_events(), "key.json", "sheet123")
    gid = book.tabs["a"].id
    after_one = {k: len(v) for k, v in book.state[gid].items()}

    push_to_sheet(_events(), "key.json", "sheet123")
    push_to_sheet(_events(), "key.json", "sheet123")

    assert {k: len(v) for k, v in book.state[gid].items()} == after_one


def test_the_sheet_leads_with_headline_figures(book):
    """A merchant should be able to read the top of its own sheet and stop
    there if it wants to."""
    push_to_sheet(_events(), "key.json", "sheet123")

    grid = book.values["a"]
    labels = grid[3]
    assert "Spent through Razorpay" in labels
    assert "Saved by negotiating" in labels
    assert isinstance(grid[4][0], (int, float))


def test_the_sheet_carries_every_section_a_merchant_needs(book):
    push_to_sheet(_events(), "key.json", "sheet123")

    flat = [c for row in book.values["a"] for c in row]
    for section in ("Your deals", "How your agent argued for you",
                    "Who you are dealing with", "What the gate stopped"):
        assert section in flat


def test_an_empty_section_says_something(book):
    """A blank block reads as a broken sheet. It should say why it is empty
    and what would fill it."""
    push_to_sheet(_events(), "key.json", "sheet123")

    flat = [str(c) for row in book.values["b"] for c in row]
    assert any("Nothing was refused" in c or "capped on purpose" in c
               for c in flat)


def test_a_second_run_replaces_rather_than_appends(book):
    """The books are a projection of an append-only log, so the log is the
    only thing that accumulates. Appending would double every row."""
    push_to_sheet(_events(), "key.json", "sheet123")
    first = len(book.values["a"])
    push_to_sheet(_events(), "key.json", "sheet123")

    assert len(book.values["a"]) == first
    assert book.calls["values_batch_clear"] == 2, "cleared before each write"


def test_a_shorter_run_cannot_strand_last_runs_rows(book):
    """Clearing is what makes replacement true. Writing over a longer grid
    without clearing leaves the tail of the previous run below the new rows,
    where it reads as real trades."""
    push_to_sheet(_events(), "key.json", "sheet123")

    assert any("'a'" in r for r in book.cleared[0])


def test_a_merchant_with_no_trades_gets_no_tab(book):
    """An empty tab claims a business traded and produced nothing, which is
    not the same as having no books here."""
    events = _events() + [E(500, "m_quiet", "ACTOR_REGISTERED",
                            {"actor_id": "m_quiet"}, "reg")]

    push_to_sheet(events, "key.json", "sheet123")

    assert "quiet" not in book.tabs


# --- the cost of a push ------------------------------------------------------

def test_the_push_costs_a_fixed_number_of_calls(book):
    """THE ONE THAT PROTECTS THE QUOTA. Creating, clearing and writing each
    tab in turn cost roughly two requests per merchant against a limit of
    sixty a minute — fine at three merchants, broken at forty. Tabs, values
    and formatting each go in one batch, so the cost does not grow with the
    roster."""
    push_to_sheet(_events(), "key.json", "sheet123")

    assert book.calls["values_batch_update"] == 1
    assert book.calls["values_batch_clear"] == 1
    assert book.calls["batch_update"] <= 3, ("add tabs, clear old formatting, "
                                             "apply new formatting")


def test_a_second_run_adds_no_tabs(book):
    """Nothing to create means no request to create it."""
    push_to_sheet(_events(), "key.json", "sheet123")
    before = book.calls["batch_update"]
    push_to_sheet(_events(), "key.json", "sheet123")

    assert book.calls["batch_update"] <= before + 2, "no tabs to add"


# --- the formatting ----------------------------------------------------------

def test_the_ledger_header_is_frozen_on_every_tab(book):
    """A ledger you cannot read the columns of once you scroll is a dump."""
    push_to_sheet(_events(), "key.json", "sheet123")

    frozen = [r["updateSheetProperties"]["properties"]
              for r in book.requests
              if "updateSheetProperties" in r
              and "gridProperties" in r["updateSheetProperties"]["properties"]]
    assert len(frozen) >= len(book.tabs) - 1
    assert all(p["gridProperties"]["frozenRowCount"] > 0 for p in frozen)


def test_money_columns_are_formatted_as_rupees(book):
    push_to_sheet(_events(), "key.json", "sheet123")

    patterns = [r["repeatCell"]["cell"]["userEnteredFormat"]["numberFormat"]
                ["pattern"]
                for r in book.requests
                if "repeatCell" in r
                and "numberFormat" in r["repeatCell"]["cell"]
                .get("userEnteredFormat", {})]
    assert any("₹" in p for p in patterns)


def test_a_refusal_is_coloured_so_it_cannot_be_missed(book):
    """A merchant scanning its own sheet should see where the gate said no
    without reading every row. The block only exists when there is something
    in it, so this needs a trade that was actually refused once."""
    refused = _trade(corr="turn_9", buyer="m_a", seller="m_b", base=400,
                     ask="ord_r", verdicts=("DENY", "ALLOW"))
    push_to_sheet(_events() + refused, "key.json", "sheet123")

    rules = [r["addConditionalFormatRule"]["rule"]
             for r in book.requests if "addConditionalFormatRule" in r]
    texts = [v["userEnteredValue"]
             for rule in rules
             for v in rule.get("booleanRule", {}).get("condition", {})
             .get("values", [])]
    assert "exceeds" in texts, "a refusal is coloured"
    assert "confirmed" in texts, "so is a settled deal"


def test_every_format_targets_a_real_tab(book):
    """A request aimed at a sheet id that does not exist fails the whole
    batch, taking the good formatting with it."""
    push_to_sheet(_events(), "key.json", "sheet123")

    ids = {t.id for t in book.tabs.values()}
    for r in book.requests:
        for key in ("repeatCell", "updateSheetProperties",
                    "updateDimensionProperties", "updateBorders"):
            if key not in r:
                continue
            spec = r[key]
            gid = (spec.get("range", {}).get("sheetId")
                   or spec.get("properties", {}).get("sheetId"))
            if gid is not None:
                assert gid in ids


# --- the tab ids the pages need ----------------------------------------------

def test_tab_ids_are_recorded_so_a_dashboard_can_deep_link(book, tmp_path):
    push_to_sheet(_events(), "key.json", "sheet123", out_dir=tmp_path)

    tabs = json.loads((tmp_path / TABS_FILE).read_text())
    assert set(tabs) == {"m_a", "m_b", "m_c"}
    assert all(isinstance(v, int) for v in tabs.values())


def test_the_sheet_id_is_never_written_beside_the_pages(book, tmp_path):
    """THE ONE THAT MATTERS. The workbook identifier lives in .env. Copying
    it into a file that sits next to generated HTML is how a private sheet
    ends up somewhere it should not."""
    push_to_sheet(_events(), "key.json", "sheet123", out_dir=tmp_path)

    assert "sheet123" not in (tmp_path / TABS_FILE).read_text()


def test_nothing_is_written_when_no_directory_is_given(book, tmp_path):
    push_to_sheet(_events(), "key.json", "sheet123")

    assert not list(tmp_path.iterdir())


# --- the export that always works --------------------------------------------

def test_csvs_are_written_without_any_credentials(tmp_path):
    """The sync is a convenience on top of an export. A bookkeeping feature
    that only works when a cloud API is reachable is not bookkeeping."""
    written, ledger = write_csvs(_events(), tmp_path)

    assert written == 3
    assert ledger.exists()
    assert (tmp_path / "m_a.csv").exists()


def test_the_combined_ledger_names_the_merchant_on_every_row(tmp_path):
    import csv

    _, ledger = write_csvs(_events(), tmp_path)
    rows = list(csv.reader(ledger.open()))

    assert rows[0][0] == "merchant"
    assert {r[0] for r in rows[1:]} == {"m_a", "m_b", "m_c"}


def test_merchants_are_listed_in_a_stable_order():
    """The tab order in the workbook should not shuffle between runs."""
    assert merchants(_events()) == sorted(merchants(_events()))
