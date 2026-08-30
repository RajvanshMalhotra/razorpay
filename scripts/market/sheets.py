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

from exchange.books import (COLUMNS, HEADINGS, entries_for,
                            sheet_rows)
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

# Tabs earlier versions of this program created. If one is no
# longer written to it holds stale figures, so it is removed —
# and only ever these, never a sheet the operator made.
LEGACY_TABS = ("All businesses",)

# One palette, applied by meaning. A tone names what a value IS — good, bad,
# waiting, worth noting — and the formatter decides what that looks like, so
# no tab picks its own colours.
INK = {"red": .10, "green": .12, "blue": .16}
PAPER = {"red": .99, "green": .99, "blue": .98}
BAND = {"red": .96, "green": .96, "blue": .94}
RULE = {"red": .87, "green": .87, "blue": .85}
WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}
QUIET = {"red": .42, "green": .42, "blue": .42}
TONES = {"good": {"red": .05, "green": .45, "blue": .30},
         "bad": {"red": .70, "green": .15, "blue": .10},
         "warn": {"red": .60, "green": .40, "blue": .02},
         "info": {"red": .10, "green": .32, "blue": .70}}

RUPEES = "\u20b9#,##0.00"
RUPEES_ROUND = "\u20b9#,##0"


def table_grid(t) -> tuple:
    """(grid, header_row). Title, subtitle, optional summary, then the table."""
    grid = [[t.title], [t.subtitle], []]
    for label, value, _is_money in t.summary:
        grid.append([label, "", "", value])
    if t.summary:
        grid.append([])
    header = len(grid)
    grid.append(list(t.headings))
    grid.extend(list(r) for r in t.rows)
    return grid, header


def table_requests(gid: int, t, grid: list, header: int) -> list:
    """Everything that turns one table into something worth reading."""
    ncols = max(len(t.headings), 5)
    rows = len(grid)
    first = header + 1
    col = {name: i for i, name in enumerate(t.headings)}
    rng = lambda r0, r1, c0=0, c1=None: {
        "sheetId": gid, "startRowIndex": r0, "endRowIndex": r1,
        "startColumnIndex": c0,
        "endColumnIndex": ncols if c1 is None else c1}

    req = [
        {"repeatCell": {
            "range": {"sheetId": gid},
            "cell": {"userEnteredFormat": {
                "backgroundColor": PAPER,
                "textFormat": {"fontFamily": "Inter", "fontSize": 10,
                               "foregroundColor": INK},
                "verticalAlignment": "TOP"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,"
                      "verticalAlignment)"}},
        {"repeatCell": {
            "range": rng(0, 1, 0, 1),
            "cell": {"userEnteredFormat": {"textFormat": {
                "fontSize": 17, "bold": True}}},
            "fields": "userEnteredFormat.textFormat"}},
        {"repeatCell": {
            "range": rng(1, 2, 0, 1),
            "cell": {"userEnteredFormat": {"textFormat": {
                "fontSize": 9, "italic": True, "foregroundColor": QUIET}}},
            "fields": "userEnteredFormat.textFormat"}},
        {"mergeCells": {"range": rng(0, 1, 0, min(ncols, 6)),
                        "mergeType": "MERGE_ROWS"}},
        {"mergeCells": {"range": rng(1, 2, 0, min(ncols, 8)),
                        "mergeType": "MERGE_ROWS"}},
        # the header: dark, and it stays put when you scroll
        {"repeatCell": {
            "range": rng(header, header + 1),
            "cell": {"userEnteredFormat": {
                "backgroundColor": INK,
                "horizontalAlignment": "LEFT",
                "verticalAlignment": "MIDDLE",
                "textFormat": {"bold": True, "fontSize": 9,
                               "foregroundColor": WHITE}}},
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,"
                      "verticalAlignment,textFormat)"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "ROWS",
                      "startIndex": header, "endIndex": header + 1},
            "properties": {"pixelSize": 30}, "fields": "pixelSize"}},
        # Rows only. A frozen first column would cut through the merged
        # title and the merged summary labels, and Sheets rejects the whole
        # batch rather than the one request.
        {"updateSheetProperties": {
            "properties": {"sheetId": gid,
                           "gridProperties": {"frozenRowCount": header + 1}},
            "fields": "gridProperties.frozenRowCount"}},
    ]
    if rows > first:
        req += [
            {"addBanding": {"bandedRange": {
                "range": rng(header, rows),
                "rowProperties": {"headerColor": INK, "firstBandColor": PAPER,
                                  "secondBandColor": BAND}}}},
            {"updateBorders": {"range": rng(header, rows),
                               "innerHorizontal": {"style": "SOLID",
                                                   "width": 1,
                                                   "color": RULE}}},
        ]

    # THE SUMMARY CANNOT SHARE THE TABLE'S COLUMNS. The first column is
    # narrow because it holds a date or a sequence number; a label like
    # "Confirmed by Razorpay" needs three times that and was being cut to
    # "Confirmed by F". Labels merge across A:C and figures across D:E.
    if t.summary:
        top, bottom = 3, 3 + len(t.summary)
        req += [
            {"repeatCell": {
                "range": rng(top, bottom, 0, 1),
                "cell": {"userEnteredFormat": {
                    "backgroundColor": BAND,
                    "textFormat": {"fontSize": 10, "foregroundColor": QUIET}}},
                "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
            {"repeatCell": {
                "range": rng(top, bottom, 3, 4),
                "cell": {"userEnteredFormat": {
                    "backgroundColor": BAND,
                    "horizontalAlignment": "LEFT",
                    "textFormat": {"bold": True, "fontSize": 12}}},
                "fields": "userEnteredFormat(backgroundColor,"
                          "horizontalAlignment,textFormat)"}},
        ]
        for i, (_l, _v, is_money) in enumerate(t.summary):
            req.append({"mergeCells": {"range": rng(top + i, top + i + 1, 0, 3),
                                       "mergeType": "MERGE_ROWS"}})
            req.append({"mergeCells": {"range": rng(top + i, top + i + 1, 3, 5),
                                       "mergeType": "MERGE_ROWS"}})
            if is_money:
                req.append({"repeatCell": {
                    "range": rng(top + i, top + i + 1, 3, 4),
                    "cell": {"userEnteredFormat": {"numberFormat": {
                        "type": "CURRENCY", "pattern": RUPEES_ROUND}}},
                    "fields": "userEnteredFormat.numberFormat"}})

    for i, w in enumerate(t.widths[:ncols]):
        req.append({"updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "COLUMNS",
                      "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"}})

    for name in t.money:
        c = col[name]
        req.append({"repeatCell": {
            "range": rng(first, rows, c, c + 1),
            "cell": {"userEnteredFormat": {
                "horizontalAlignment": "RIGHT",
                "numberFormat": {"type": "CURRENCY", "pattern": RUPEES}}},
            "fields": "userEnteredFormat(horizontalAlignment,numberFormat)"}})
    for name in t.counts:
        c = col[name]
        req.append({"repeatCell": {
            "range": rng(first, rows, c, c + 1),
            "cell": {"userEnteredFormat": {
                "horizontalAlignment": "RIGHT",
                "numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
            "fields": "userEnteredFormat(horizontalAlignment,numberFormat)"}})
    # Identifiers are for looking up, not for reading.
    for name in t.ident:
        c = col[name]
        req.append({"repeatCell": {
            "range": rng(first, rows, c, c + 1),
            "cell": {"userEnteredFormat": {"textFormat": {
                "fontFamily": "Roboto Mono", "fontSize": 8,
                "foregroundColor": QUIET}}},
            "fields": "userEnteredFormat.textFormat"}})
    # Prose wraps; everything else is clipped, so one long sentence cannot
    # make every row in the table tall.
    for name in t.wrap:
        c = col[name]
        req.append({"repeatCell": {
            "range": rng(first, rows, c, c + 1),
            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
            "fields": "userEnteredFormat.wrapStrategy"}})
    for name in t.headings:
        if name not in t.wrap:
            c = col[name]
            req.append({"repeatCell": {
                "range": rng(first, rows, c, c + 1),
                "cell": {"userEnteredFormat": {"wrapStrategy": "CLIP"}},
                "fields": "userEnteredFormat.wrapStrategy"}})

    for n, (name, contains, tone) in enumerate(t.rules):
        c = col[name]
        req.append({"addConditionalFormatRule": {"index": n, "rule": {
            "ranges": [rng(first, rows, c, c + 1)],
            "booleanRule": {
                "condition": {"type": "TEXT_CONTAINS",
                              "values": [{"userEnteredValue": contains}]},
                "format": {"textFormat": {"bold": True,
                                          "foregroundColor": TONES[tone]}}}}}})
    return req


def _cleanup_requests(book, sheet_ids: set) -> list:
    """Remove the banding, rules and merges a previous push added.

    All three are additive. Sheets refuses a second banding over the same
    range outright and accepts duplicate rules silently, so a tab would carry
    more of them after every run. Values are replaced on each push; the
    formatting has to be too.

    Only tabs this program manages are touched. Anything the operator added
    to a sheet of their own is left exactly as they left it.
    """
    try:
        meta = book.fetch_sheet_metadata()
    except AttributeError:            # a transport that does not model it
        return []

    req = []
    for sheet in meta.get("sheets", []):
        gid = sheet.get("properties", {}).get("sheetId")
        if gid not in sheet_ids:
            continue
        for band in sheet.get("bandedRanges", []) or ():
            req.append({"deleteBanding": {"bandedRangeId": band["bandedRangeId"]}})
        if sheet.get("merges"):
            req.append({"unmergeCells": {"range": {"sheetId": gid}}})
        # Deleting by index shifts every later index down, so walk backwards.
        rules = sheet.get("conditionalFormats", []) or ()
        for i in range(len(rules) - 1, -1, -1):
            req.append({"deleteConditionalFormatRule":
                        {"sheetId": gid, "index": i}})
    return req


def push_to_sheet(events, key_file: str, sheet_id: str, limit: int | None = None,
                  out_dir: pathlib.Path | None = None, roster=None):
    """The whole market as a workbook: market-wide tabs, then one per business.

    THREE CALLS, NOT NINETY. An earlier version created, cleared and wrote
    each tab one at a time — around two write requests per merchant against a
    quota of sixty a minute, which worked only because the market is small.
    Tabs are created in one batch, values written in one batch, and formatting
    applied in one batch, so the cost does not grow with the roster.

    Records each merchant's tab id on the way out, so a dashboard can link
    straight to its own tab rather than to the top of a shared workbook.
    """
    import gspread
    from google.oauth2.service_account import Credentials

    from exchange.workbook import market_tables, merchant_table

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(key_file, scopes=scopes)
    book = gspread.authorize(creds).open_by_key(sheet_id)

    roster = roster if roster is not None else merchants(events)
    tables = list(market_tables(events, roster))
    owners = {}
    for actor in merchants(events)[:limit]:
        books = entries_for(events, actor)
        if books.entries:
            table = merchant_table(books)
            tables.append(table)
            owners[actor] = table.key

    built = {t.key: (t,) + table_grid(t) for t in tables}

    existing = {w.title: w for w in book.worksheets()}
    adds = [{"addSheet": {"properties": {
                "title": key,
                "gridProperties": {"rowCount": max(len(grid) + 6, 40),
                                   "columnCount": max(len(t.headings) + 1, 8)}}}}
            for key, (t, grid, _h) in built.items() if key not in existing]
    if adds:
        book.batch_update({"requests": adds})
        existing = {w.title: w for w in book.worksheets()}

    # Clearing first, so a shorter run cannot leave last run's rows stranded
    # below the new ones where they read as real records.
    book.values_batch_clear({"ranges": [f"'{k}'" for k in built]})
    book.values_batch_update({
        "valueInputOption": "USER_ENTERED",
        "data": [{"range": f"'{k}'!A1", "values": grid}
                 for k, (_t, grid, _h) in built.items()]})

    # A tab an earlier version of this program created and no longer writes
    # to would sit there forever holding stale figures. Only tabs we know we
    # made are removed; the operator's own sheets are never touched.
    stale = [existing[t].id for t in LEGACY_TABS
             if t in existing and t not in built]
    fmt = [{"deleteSheet": {"sheetId": gid}} for gid in stale]
    for t in LEGACY_TABS:
        existing.pop(t, None) if t in [k for k in existing
                                       if k in LEGACY_TABS
                                       and k not in built] else None

    fmt += _cleanup_requests(book, {existing[k].id for k in built
                                    if k in existing})
    # The workbook should open on the front page, not on whichever tab was
    # touched last. Moving it is non-destructive.
    fmt.append({"updateSheetProperties": {
        "properties": {"sheetId": existing[tables[0].key].id, "index": 0},
        "fields": "index"}})
    for key, (t, grid, header) in built.items():
        fmt += table_requests(existing[key].id, t, grid, header)
    book.batch_update({"requests": fmt})

    tabs = {actor: existing[key].id for actor, key in owners.items()}
    if out_dir is not None:
        # The sheet id itself is NOT written here. It lives in .env, and
        # duplicating a private workbook's identifier into a file that sits
        # beside generated pages is how it ends up somewhere it should not.
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / TABS_FILE).write_text(
            json.dumps(tabs, indent=2, sort_keys=True), encoding="utf-8")
    return list(owners)


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
        # Names exactly what is missing and never echoes a value — an error
        # message is a place secrets leak. It used to say "no credentials"
        # when only one of the two was absent, which sent you looking for a
        # problem you had already solved.
        missing = [name for name, value in
                   (("GOOGLE_SERVICE_ACCOUNT_FILE", key_file),
                    ("GOOGLE_SHEET_ID", sheet_id)) if not value]
        print(f"\n  Nothing was synced: {' and '.join(missing)} "
              f"{'is' if len(missing) == 1 else 'are'} not set in .env.")
        print("  The CSVs above are complete and import into Sheets as they are.")
        if "GOOGLE_SERVICE_ACCOUNT_FILE" in missing:
            print("    GOOGLE_SERVICE_ACCOUNT_FILE=/absolute/path/to/key.json")
        if "GOOGLE_SHEET_ID" in missing:
            print("    GOOGLE_SHEET_ID=<the id between /d/ and /edit in the "
                  "sheet URL>")
            print("  and share that sheet with the service account's "
                  "client_email as an Editor.")
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
