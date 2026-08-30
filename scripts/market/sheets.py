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

# Where things sit on a merchant's tab. The title and its subtitle come
# first, then the summary block, then the ledger — the same order the
# dashboard uses, because a merchant should not have to learn a second
# layout to read its own books.
TITLE_ROWS = 3

INK = {"red": .10, "green": .12, "blue": .16}
PAPER = {"red": .97, "green": .96, "blue": .93}
BAND = {"red": .99, "green": .98, "blue": .96}
WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}
GREEN = {"red": .05, "green": .45, "blue": .30}
AMBER = {"red": .60, "green": .40, "blue": .02}
RED = {"red": .70, "green": .15, "blue": .10}
BLUE = {"red": .10, "green": .32, "blue": .70}

# Column widths in pixels, in COLUMNS order. An item description needs room;
# a quantity does not, and letting them share a width makes both wrong.
WIDTHS = (92, 82, 150, 300, 62, 104, 104, 92, 168, 178, 178, 62, 260)

# The money columns, so they can be formatted as rupees rather than as bare
# numbers a reader has to guess the units of.
MONEY_COLS = ("unit_price_inr", "amount_inr")

# Summary rows whose figure is money, by the label the grid shows.
MONEY_SUMMARY = ("Purchases", "Sales", "Net",
                 "Confirmed by Razorpay",
                 "Value awaiting confirmation")


def tab_grid(books):
    """The full tab: a title, the summary, then the ledger."""
    name = books.actor_id[2:].replace("_", " ").title()
    grid = [[name], ["Books kept automatically from the exchange audit trail"],
            []]
    for label, value in books.summary():
        # The marker stays in `summary()` so the formatter can find the money
        # rows; on the page it is redundant once the cell says rupees itself.
        grid.append([label.replace(" (\u20b9)", ""), "", "", value])
    grid += [[], list(HEADINGS)]
    grid.extend(entry.row() for entry in books.entries)
    return grid


def _fmt_requests(gid: int, grid: list, rows: int) -> list:
    """Everything that makes one tab readable, as one batch of requests."""
    header = next(i for i, r in enumerate(grid) if r == list(HEADINGS))
    first_entry = header + 1
    summary_top, summary_bottom = TITLE_ROWS, header - 1
    ncols = len(COLUMNS)
    rng = lambda r0, r1, c0=0, c1=ncols: {
        "sheetId": gid, "startRowIndex": r0, "endRowIndex": r1,
        "startColumnIndex": c0, "endColumnIndex": c1}

    req = [
        # the whole sheet: paper, and a readable face
        {"repeatCell": {
            "range": {"sheetId": gid},
            "cell": {"userEnteredFormat": {
                "backgroundColor": PAPER,
                "textFormat": {"fontFamily": "Inter", "fontSize": 10,
                               "foregroundColor": INK},
                "verticalAlignment": "MIDDLE"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,"
                      "verticalAlignment)"}},
        # the title
        {"repeatCell": {
            "range": rng(0, 1, 0, 1),
            "cell": {"userEnteredFormat": {"textFormat": {
                "fontSize": 16, "bold": True, "foregroundColor": INK}}},
            "fields": "userEnteredFormat.textFormat"}},
        {"repeatCell": {
            "range": rng(1, 2, 0, 1),
            "cell": {"userEnteredFormat": {"textFormat": {
                "fontSize": 9, "italic": True,
                "foregroundColor": {"red": .45, "green": .45, "blue": .45}}}},
            "fields": "userEnteredFormat.textFormat"}},
        # THE SUMMARY CANNOT SHARE THE LEDGER'S COLUMNS. Column A is 92px
        # because it holds a date; a label like "Confirmed by Razorpay" needs
        # three times that, and it was being cut to "Confirmed by F". Labels
        # merge across A:C and figures across D:E, so each block gets the
        # width it needs without either dictating to the other.
        {"repeatCell": {
            "range": rng(summary_top, summary_bottom, 0, 1),
            "cell": {"userEnteredFormat": {
                "backgroundColor": BAND,
                "textFormat": {"bold": False, "fontSize": 10,
                               "foregroundColor": {"red": .35, "green": .35,
                                                   "blue": .35}}}},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
        {"repeatCell": {
            "range": rng(summary_top, summary_bottom, 3, 4),
            "cell": {"userEnteredFormat": {
                "backgroundColor": BAND,
                "horizontalAlignment": "LEFT",
                "textFormat": {"bold": True, "fontSize": 12}}},
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,"
                      "textFormat)"}},
        # the ledger header: dark, and it stays put when you scroll
        {"repeatCell": {
            "range": rng(header, header + 1),
            "cell": {"userEnteredFormat": {
                "backgroundColor": INK,
                "horizontalAlignment": "LEFT",
                "textFormat": {"bold": True, "fontSize": 9,
                               "foregroundColor": WHITE}}},
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,"
                      "textFormat)"}},
        {"updateSheetProperties": {
            "properties": {"sheetId": gid, "gridProperties": {
                "frozenRowCount": header + 1}},
            "fields": "gridProperties.frozenRowCount"}},
        # every row a readable height, and one band so the eye can track
        {"addBanding": {"bandedRange": {
            "range": rng(header, rows),
            "rowProperties": {"headerColor": INK, "firstBandColor": PAPER,
                              "secondBandColor": BAND}}}},
        {"updateBorders": {
            "range": rng(header, rows),
            "innerHorizontal": {"style": "SOLID", "width": 1,
                                "color": {"red": .88, "green": .87,
                                          "blue": .84}}}},
    ]

    # merge each summary row into a label cell and a figure cell
    for r in range(summary_top, summary_bottom):
        req.append({"mergeCells": {"range": rng(r, r + 1, 0, 3),
                                   "mergeType": "MERGE_ROWS"}})
        req.append({"mergeCells": {"range": rng(r, r + 1, 3, 5),
                                   "mergeType": "MERGE_ROWS"}})

    # a figure the label calls rupees should look like rupees
    for i, row in enumerate(grid[summary_top:summary_bottom]):
        if row and row[0] in MONEY_SUMMARY:
            req.append({"repeatCell": {
                "range": rng(summary_top + i, summary_top + i + 1, 3, 4),
                "cell": {"userEnteredFormat": {"numberFormat": {
                    "type": "CURRENCY", "pattern": '\u20b9#,##0'}}},
                "fields": "userEnteredFormat.numberFormat"}})

    # column widths
    for i, w in enumerate(WIDTHS[:ncols]):
        req.append({"updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "COLUMNS",
                      "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"}})

    # rupees where the number is money, plain where it is a count
    for name in MONEY_COLS:
        c = COLUMNS.index(name)
        req.append({"repeatCell": {
            "range": rng(first_entry, rows, c, c + 1),
            "cell": {"userEnteredFormat": {
                "horizontalAlignment": "RIGHT",
                "numberFormat": {"type": "CURRENCY",
                                 "pattern": '\u20b9#,##0.00'}}},
            "fields": "userEnteredFormat(horizontalAlignment,numberFormat)"}})
    qty = COLUMNS.index("qty")
    req.append({"repeatCell": {
        "range": rng(first_entry, rows, qty, qty + 1),
        "cell": {"userEnteredFormat": {"horizontalAlignment": "RIGHT",
                                       "numberFormat": {"type": "NUMBER",
                                                        "pattern": "#,##0"}}},
        "fields": "userEnteredFormat(horizontalAlignment,numberFormat)"}})

    # ids are for looking up, not for reading: small and quiet
    for name in ("razorpay_order_id", "razorpay_payment_id", "correlation_id",
                 "event"):
        c = COLUMNS.index(name)
        req.append({"repeatCell": {
            "range": rng(first_entry, rows, c, c + 1),
            "cell": {"userEnteredFormat": {"textFormat": {
                "fontFamily": "Roboto Mono", "fontSize": 8,
                "foregroundColor": {"red": .42, "green": .42, "blue": .42}}}},
            "fields": "userEnteredFormat.textFormat"}})

    # status and gate carry meaning, so they carry colour
    status = COLUMNS.index("status")
    gate = COLUMNS.index("gate")
    rules = [
        (status, "settled", GREEN), (status, "repaired", BLUE),
        (status, "pending", AMBER), (gate, "DENY", RED),
    ]
    for n, (col, text, colour) in enumerate(rules):
        req.append({"addConditionalFormatRule": {"index": n, "rule": {
            "ranges": [rng(first_entry, rows, col, col + 1)],
            "booleanRule": {
                "condition": {"type": "TEXT_CONTAINS",
                              "values": [{"userEnteredValue": text}]},
                "format": {"textFormat": {"bold": True,
                                          "foregroundColor": colour}}}}}})
    return req


def _index_grid(all_books) -> list:
    """A front page, so the workbook opens on something worth reading."""
    grid = [["Agent Exchange — merchant books"],
            ["One tab per business. Every figure is read from the exchange's "
             "audit trail; nothing here was typed by hand."],
            [],
            ["Business", "Purchases", "Sales", "Net",
             "Confirmed by Razorpay", "Awaiting", "Transactions"]]
    for b in sorted(all_books, key=lambda x: -x.bought_inr):
        awaiting = round(sum(e.amount_inr for e in b.entries
                             if e.status == "pending"), 2)
        grid.append([b.actor_id[2:].replace("_", " ").title(),
                     b.bought_inr, b.sold_inr, b.net_inr, b.settled_inr,
                     awaiting, len(b.entries)])
    return grid


def _index_requests(gid: int, rows: int) -> list:
    rng = lambda r0, r1, c0=0, c1=7: {
        "sheetId": gid, "startRowIndex": r0, "endRowIndex": r1,
        "startColumnIndex": c0, "endColumnIndex": c1}
    req = [
        {"repeatCell": {
            "range": {"sheetId": gid},
            "cell": {"userEnteredFormat": {
                "backgroundColor": PAPER,
                "textFormat": {"fontFamily": "Inter", "fontSize": 10,
                               "foregroundColor": INK}}},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
        {"repeatCell": {
            "range": rng(0, 1, 0, 1),
            "cell": {"userEnteredFormat": {"textFormat": {
                "fontSize": 18, "bold": True}}},
            "fields": "userEnteredFormat.textFormat"}},
        {"repeatCell": {
            "range": rng(1, 2, 0, 1),
            "cell": {"userEnteredFormat": {"textFormat": {
                "fontSize": 9, "italic": True,
                "foregroundColor": {"red": .45, "green": .45, "blue": .45}}}},
            "fields": "userEnteredFormat.textFormat"}},
        {"repeatCell": {
            "range": rng(3, 4),
            "cell": {"userEnteredFormat": {
                "backgroundColor": INK,
                "textFormat": {"bold": True, "fontSize": 9,
                               "foregroundColor": WHITE}}},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
        {"updateSheetProperties": {
            "properties": {"sheetId": gid,
                           "gridProperties": {"frozenRowCount": 4}},
            "fields": "gridProperties.frozenRowCount"}},
        {"repeatCell": {
            "range": rng(4, rows, 1, 6),
            "cell": {"userEnteredFormat": {
                "horizontalAlignment": "RIGHT",
                "numberFormat": {"type": "CURRENCY",
                                 "pattern": '\u20b9#,##0'}}},
            "fields": "userEnteredFormat(horizontalAlignment,numberFormat)"}},
        {"addBanding": {"bandedRange": {
            "range": rng(3, rows),
            "rowProperties": {"headerColor": INK, "firstBandColor": PAPER,
                              "secondBandColor": BAND}}}},
    ]
    for i, w in enumerate((190, 116, 106, 116, 176, 116, 106)):
        req.append({"updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "COLUMNS",
                      "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"}})
    # a negative net is a business spending more than it earns; say so in red
    req.append({"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [rng(4, rows, 3, 4)],
        "booleanRule": {
            "condition": {"type": "NUMBER_LESS",
                          "values": [{"userEnteredValue": "0"}]},
            "format": {"textFormat": {"foregroundColor": RED}}}}}})
    return req


def _cleanup_requests(book, sheet_ids: set) -> list:
    """Remove the banding and rules a previous push added to our own tabs.

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
        # Merges are additive too: re-merging a merged range errors, and a
        # shorter run would leave last run's merges over the wrong rows.
        if sheet.get("merges"):
            req.append({"unmergeCells": {"range": {"sheetId": gid}}})
        # Deleting by index shifts every later index down, so walk backwards.
        rules = sheet.get("conditionalFormats", []) or ()
        for i in range(len(rules) - 1, -1, -1):
            req.append({"deleteConditionalFormatRule":
                        {"sheetId": gid, "index": i}})
    return req


def push_to_sheet(events, key_file: str, sheet_id: str, limit: int | None = None,
                  out_dir: pathlib.Path | None = None):
    """One tab per merchant, replaced wholesale, then formatted to be read.

    Replaced rather than appended: these books are a projection of an
    append-only log, so the log is the only thing that accumulates. Appending
    here would double every row on a second run and quietly make the sheet
    disagree with its own source.

    THREE CALLS, NOT NINETY. An earlier version created, cleared and wrote
    each tab one at a time — around sixty write requests against a quota of
    sixty a minute, which worked only because the market is small. Tabs are
    created in one batch, values written in one batch, and formatting applied
    in one batch, so the cost does not grow with the roster.

    Records each merchant's tab id on the way out, so a dashboard can link
    straight to its own tab rather than to the top of a shared workbook.
    """
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(key_file, scopes=scopes)
    book = gspread.authorize(creds).open_by_key(sheet_id)

    wanted = []
    for actor in merchants(events)[:limit]:
        books = entries_for(events, actor)
        if books.entries:
            wanted.append((actor, actor[2:][:99] or actor, books))

    existing = {w.title: w for w in book.worksheets()}
    index_title = "All businesses"
    grids = {index_title: _index_grid([b for _, _, b in wanted])}
    for _, title, books in wanted:
        grids[title] = tab_grid(books)

    # 1. every missing tab, in one request
    adds = [{"addSheet": {"properties": {
                "title": title,
                "gridProperties": {"rowCount": max(len(grid) + 4, 40),
                                   "columnCount": max(len(COLUMNS), 8)}}}}
            for title, grid in grids.items() if title not in existing]
    if adds:
        book.batch_update({"requests": adds})
        existing = {w.title: w for w in book.worksheets()}

    # 2. every value, in one request — clearing first so a shorter run cannot
    #    leave last run's rows stranded below the new ones
    book.values_batch_clear(
        {"ranges": [f"'{t}'" for t in grids]})
    book.values_batch_update({
        "valueInputOption": "USER_ENTERED",
        "data": [{"range": f"'{t}'!A1", "values": g}
                 for t, g in grids.items()]})

    # 3. every format, in one request
    # CLEAR WHAT THE LAST RUN LEFT BEFORE FORMATTING AGAIN. Banding and
    # conditional-format rules are additive: Sheets refuses a second banding
    # over the same range outright, and it accepts duplicate rules silently,
    # so a tab would carry four more of them after every push. Values are
    # replaced on each run; the formatting has to be too.
    ours = {existing[t].id for t in grids if t in existing}
    fmt = _cleanup_requests(book, ours)

    # The workbook should open on the front page, not on whichever tab was
    # touched last or on the empty default sheet Google creates. Moving it is
    # non-destructive: nothing of the operator's is deleted or renamed.
    fmt.append({"updateSheetProperties": {
        "properties": {"sheetId": existing[index_title].id, "index": 0},
        "fields": "index"}})
    fmt += _index_requests(existing[index_title].id,
                           len(grids[index_title]))
    for _, title, books in wanted:
        fmt += _fmt_requests(existing[title].id, grids[title],
                             len(grids[title]))
    book.batch_update({"requests": fmt})

    tabs = {actor: existing[title].id for actor, title, _ in wanted}
    pushed = [actor for actor, _, _ in wanted]

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
