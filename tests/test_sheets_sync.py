"""The Google Sheets push, exercised without a Google account.

This is the one part of the bookkeeping that cannot be run here — it needs a
service-account key that belongs to the operator, not to the repository. So
the transport is faked and the behaviour that matters is pinned: what gets
written, that a second run does not double it, and that each merchant's tab
id comes back so its dashboard can link straight to it.
"""
import json

import pytest

from exchange.books import COLUMNS, entries_for
from scripts.market.sheets import TABS_FILE, merchants, push_to_sheet, write_csvs
from tests.test_books import E, _trade


class FakeTab:
    def __init__(self, title, tab_id):
        self.title = title
        self.id = tab_id
        self.rows = None
        self.clears = 0

    def clear(self):
        self.clears += 1
        self.rows = None

    def update(self, values=None, range_name=None):
        self.rows = values
        self.range_name = range_name


class FakeBook:
    def __init__(self, existing=()):
        self.tabs = {t: FakeTab(t, 100 + n) for n, t in enumerate(existing)}
        self.added = []

    def worksheet(self, title):
        import gspread
        if title not in self.tabs:
            raise gspread.WorksheetNotFound(title)
        return self.tabs[title]

    def add_worksheet(self, title, rows, cols):
        tab = FakeTab(title, 900 + len(self.added))
        self.tabs[title] = tab
        self.added.append(title)
        return tab


@pytest.fixture
def patched(monkeypatch):
    """Stand in for gspread and google-auth, keeping WorksheetNotFound real."""
    import gspread

    book = FakeBook()
    monkeypatch.setattr(gspread, "authorize",
                        lambda creds: type("C", (), {"open_by_key":
                                                     lambda s, k: book})())
    import google.oauth2.service_account as sa
    monkeypatch.setattr(sa.Credentials, "from_service_account_file",
                        classmethod(lambda cls, f, scopes=None: object()))
    return book


def _events():
    return (_trade(corr="turn_1", buyer="m_a", seller="m_b",
                   amount=490_000, ask="ord_b")
            + _trade(corr="turn_2", buyer="m_c", seller="m_a",
                     amount=300_000, base=200, ask="ord_a"))


# --- what lands in the sheet -------------------------------------------------

def test_each_merchant_gets_its_own_tab(patched):
    pushed = push_to_sheet(_events(), "key.json", "sheet123")

    assert sorted(pushed) == ["m_a", "m_b", "m_c"]
    assert sorted(patched.tabs) == ["a", "b", "c"]


def test_the_tab_holds_the_same_grid_the_page_shows(patched):
    """One function builds both, so the sheet and the dashboard can never
    disagree about what a merchant bought."""
    push_to_sheet(_events(), "key.json", "sheet123")

    rows = patched.tabs["a"].rows
    assert rows[0] == ["Merchant", "m_a"]
    assert list(COLUMNS) in rows
    header = rows.index(list(COLUMNS))
    assert len(rows) - header - 1 == len(entries_for(_events(), "m_a").entries)


def test_a_second_run_replaces_rather_than_appends(patched):
    """The books are a projection of an append-only log, so the log is the
    only thing that accumulates. Appending would double every row."""
    push_to_sheet(_events(), "key.json", "sheet123")
    first = len(patched.tabs["a"].rows)
    push_to_sheet(_events(), "key.json", "sheet123")

    assert patched.tabs["a"].clears == 1, "existing tab was cleared, not grown"
    assert len(patched.tabs["a"].rows) == first


def test_a_merchant_with_no_trades_gets_no_tab(patched):
    """An empty tab is a claim that a business exists on the exchange and did
    nothing, which is not the same as it having no books here."""
    events = _events() + [E(500, "m_quiet", "ACTOR_REGISTERED",
                            {"actor_id": "m_quiet"}, "reg")]

    push_to_sheet(events, "key.json", "sheet123")

    assert "quiet" not in patched.tabs


# --- the tab ids the pages need ----------------------------------------------

def test_tab_ids_are_recorded_so_a_dashboard_can_deep_link(patched, tmp_path):
    push_to_sheet(_events(), "key.json", "sheet123", out_dir=tmp_path)

    tabs = json.loads((tmp_path / TABS_FILE).read_text())
    assert set(tabs) == {"m_a", "m_b", "m_c"}
    assert all(isinstance(v, int) for v in tabs.values())


def test_the_sheet_id_is_never_written_beside_the_pages(patched, tmp_path):
    """THE ONE THAT MATTERS. The workbook identifier lives in .env. Copying
    it into a file that sits next to generated HTML is how a private sheet
    ends up somewhere it should not."""
    push_to_sheet(_events(), "key.json", "sheet123", out_dir=tmp_path)

    assert "sheet123" not in (tmp_path / TABS_FILE).read_text()


def test_nothing_is_written_when_no_directory_is_given(patched, tmp_path):
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
