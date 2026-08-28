"""Push every merchant's books to a real Google Sheet, or to CSV.

    # CSVs, always, no credentials needed
    .venv/bin/python -m scripts.market.sheets runs/market.db

    # a live Google Sheet, one tab per merchant
    .venv/bin/python -m scripts.market.sheets runs/market.db --sheet

CREDENTIALS ARE YOURS AND STAY YOURS. This reads the path to a Google service
account key from `GOOGLE_SERVICE_ACCOUNT_FILE` in your `.env` and hands it
straight to google-auth. The key is never read into this program's own
variables, never logged, and never printed — including in errors, which name
the path and not its contents.

To set it up once:

  1. console.cloud.google.com → a project → enable the Google Sheets API
  2. IAM → Service accounts → create one → Keys → add key → JSON
  3. save the JSON somewhere outside this repository
  4. put two lines in .env:
       GOOGLE_SERVICE_ACCOUNT_FILE=/absolute/path/to/key.json
       GOOGLE_SHEET_ID=<the id from the sheet's URL>
  5. open the sheet and Share it with the service account's client_email,
     as an Editor

WHY A SERVICE ACCOUNT RATHER THAN OAUTH. A merchant's books should sync
without a person clicking a consent screen every hour, which is the whole
point of calling it automatic bookkeeping.

WITHOUT CREDENTIALS THIS STILL DOES THE USEFUL THING. It writes one CSV per
merchant plus a combined ledger, and any of them opens in Sheets by dragging
it into Drive. The sync is a convenience on top of an export that always
works — a bookkeeping feature that only functions when a cloud API is
reachable is not bookkeeping.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import sys

from exchange.books import COLUMNS, entries_for, sheet_rows
from exchange.eventlog import EventLog


def merchants(events) -> list[str]:
    return sorted({e.actor_id for e in events if e.actor_id.startswith("m_")})


def write_csvs(events, out_dir: pathlib.Path) -> tuple[int, pathlib.Path]:
    """One file per merchant, plus one combined ledger across the market."""
    out_dir.mkdir(parents=True, exist_ok=True)
    combined = [["merchant", *COLUMNS]]

    written = 0
    for actor in merchants(events):
        books = entries_for(events, actor)
        if not books.entries:
            continue
        path = out_dir / f"{actor}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerows(sheet_rows(books))
        combined.extend([actor, *entry.row()] for entry in books.entries)
        written += 1

    ledger = out_dir / "all-trades.csv"
    with ledger.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(combined)
    return written, ledger


TABS_FILE = "sheet-tabs.json"


def push_to_sheet(events, key_file: str, sheet_id: str, limit: int | None = None,
                  out_dir: pathlib.Path | None = None):
    """One tab per merchant, replaced wholesale on each run.

    Replaced rather than appended: these books are a projection of an
    append-only log, so the log is the only thing that accumulates. Appending
    here would double every row on a second run and quietly make the sheet
    disagree with its own source.

    Records each merchant's tab id on the way out. A merchant's dashboard
    links straight to its own tab rather than to the top of a shared
    workbook, and a tab id is the only thing that makes that possible — so
    the push writes down what it learns instead of throwing it away.
    """
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(key_file, scopes=scopes)
    book = gspread.authorize(creds).open_by_key(sheet_id)

    pushed, tabs = [], {}
    for actor in merchants(events)[:limit]:
        books = entries_for(events, actor)
        if not books.entries:
            continue
        title = actor[2:][:99] or actor
        try:
            tab = book.worksheet(title)
            tab.clear()
        except gspread.WorksheetNotFound:
            tab = book.add_worksheet(title=title, rows=200, cols=len(COLUMNS))
        tab.update(values=sheet_rows(books), range_name="A1")
        pushed.append(actor)
        tabs[actor] = tab.id

    if out_dir is not None:
        # The sheet id itself is NOT written here. It lives in .env, and
        # duplicating a private workbook's identifier into a file that sits
        # beside generated pages is how it ends up somewhere it should not.
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / TABS_FILE).write_text(
            json.dumps(tabs, indent=2, sort_keys=True), encoding="utf-8")
    return pushed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Merchant books to CSV or Sheets.")
    parser.add_argument("db", nargs="?", default="runs/market.db")
    parser.add_argument("--out", default="runs/books")
    parser.add_argument("--sheet", action="store_true",
                        help="also push to the Google Sheet in .env")
    parser.add_argument("--only", type=int, default=None,
                        help="push only the first N merchants")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv()

    log = EventLog(args.db)
    try:
        events = log.read_all()
    finally:
        log.close()

    written, ledger = write_csvs(events, pathlib.Path(args.out))
    print(f"  {written} merchant books written to {args.out}/")
    print(f"  combined ledger: {ledger}")

    if not args.sheet:
        print("\n  Drag any of those into Google Drive to open it as a Sheet,")
        print("  or re-run with --sheet to sync automatically.")
        return 0

    key_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not key_file or not sheet_id:
        # Deliberately names what is missing and where it goes, and never
        # echoes a value — an error message is a place secrets leak.
        print("\n  No Google credentials configured, so nothing was synced.")
        print("  The CSVs above are complete and import into Sheets as they are.")
        print("  To sync automatically, add to .env:")
        print("    GOOGLE_SERVICE_ACCOUNT_FILE=/absolute/path/to/key.json")
        print("    GOOGLE_SHEET_ID=<id from the sheet URL>")
        print("  then share the sheet with the service account's client_email.")
        return 1
    if not pathlib.Path(key_file).exists():
        print(f"\n  GOOGLE_SERVICE_ACCOUNT_FILE points at a file that is not "
              f"there: {key_file}")
        return 1

    try:
        pushed = push_to_sheet(events, key_file, sheet_id, args.only,
                               out_dir=pathlib.Path(args.out))
    except Exception as error:  # noqa: BLE001 — the cause belongs on screen
        print(f"\n  Google refused the sync: {type(error).__name__}: {error}")
        print("  The usual cause is the sheet not being shared with the "
              "service account's client_email as an Editor.")
        return 1

    print(f"\n  synced {len(pushed)} tabs to "
          f"https://docs.google.com/spreadsheets/d/{sheet_id}")
    print(f"  tab ids written to {args.out}/{TABS_FILE} — rebuild the pages "
          f"and each merchant links straight to its own tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
