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

# Tabs earlier versions of this program created. If one is no longer written
# to it holds stale figures, so it is removed — and only ever these, never a
# sheet the operator made.
LEGACY_TABS = ("All businesses", "Overview", "Campaign board", "Auction",
               "Negotiations", "Gate decisions", "Who dealt with whom",
               "What agents learned")

# One palette, applied by meaning. A tone names what a value IS; the renderer
# decides what that looks like, so no block picks its own colours.
INK = {"red": .09, "green": .11, "blue": .15}
PAPER = {"red": 1.0, "green": 1.0, "blue": 1.0}
BAND = {"red": .97, "green": .97, "blue": .96}
RULE = {"red": .87, "green": .87, "blue": .86}
WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}
QUIET = {"red": .44, "green": .44, "blue": .44}
BRAND = {"red": .13, "green": .35, "blue": .74}
BRAND_WASH = {"red": .90, "green": .94, "blue": 1.0}
GOOD = {"red": .04, "green": .45, "blue": .29}
GOOD_WASH = {"red": .89, "green": .96, "blue": .93}
WARN = {"red": .62, "green": .38, "blue": .02}
WARN_WASH = {"red": 1.0, "green": .96, "blue": .87}
BAD = {"red": .70, "green": .14, "blue": .10}
TONES = {"good": GOOD, "bad": BAD, "warn": WARN, "info": BRAND}
CARD_TONE = {"money": (BRAND, BRAND_WASH), "good": (GOOD, GOOD_WASH),
             "warn": (WARN, WARN_WASH), "count": (INK, BAND)}

RUPEES = "\u20b9#,##0.00"
RUPEES_ROUND = "\u20b9#,##0"
CARD_SPAN = 2          # columns per headline card


def sheet_grid(sheet) -> tuple:
    """(grid, layout). Layout records where each piece landed, so the
    formatter never has to guess a row number."""
    grid = [[sheet.title], [sheet.subtitle], []]
    layout = {"cards": len(grid), "blocks": []}

    labels, values = [], []
    for label, value, _kind in sheet.cards:
        labels += [label] + [""] * (CARD_SPAN - 1)
        values += [value] + [""] * (CARD_SPAN - 1)
    grid += [labels, values, []]

    for block in sheet.blocks:
        grid.append([block.heading])
        grid.append([block.note])
        head = len(grid)
        grid.append(list(block.headings))
        rows = [list(r) for r in block.rows] or [[block.empty]]
        grid.extend(rows)
        grid.append([])
        layout["blocks"].append(
            {"block": block, "title": head - 2, "head": head,
             "first": head + 1, "last": head + 1 + len(rows),
             "empty": not block.rows})
    return grid, layout


def sheet_requests(gid: int, sheet, grid: list, layout: dict) -> list:
    """Everything that turns one merchant's sheet into a dashboard."""
    span = max(len(b.headings) for b in sheet.blocks) if sheet.blocks else 6
    span = max(span, len(sheet.cards) * CARD_SPAN)
    rng = lambda r0, r1, c0=0, c1=None: {
        "sheetId": gid, "startRowIndex": r0, "endRowIndex": r1,
        "startColumnIndex": c0, "endColumnIndex": span if c1 is None else c1}

    cards_row = layout["cards"]
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
        # the masthead
        {"repeatCell": {
            "range": rng(0, 1, 0, 1),
            "cell": {"userEnteredFormat": {"textFormat": {
                "fontSize": 20, "bold": True}}},
            "fields": "userEnteredFormat.textFormat"}},
        {"repeatCell": {
            "range": rng(1, 2, 0, 1),
            "cell": {"userEnteredFormat": {"textFormat": {
                "fontSize": 10, "italic": True, "foregroundColor": QUIET}}},
            "fields": "userEnteredFormat.textFormat"}},
        {"mergeCells": {"range": rng(0, 1, 0, min(span, 5)),
                        "mergeType": "MERGE_ROWS"}},
        {"mergeCells": {"range": rng(1, 2, 0, min(span, 9)),
                        "mergeType": "MERGE_ROWS"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "ROWS",
                      "startIndex": cards_row + 1, "endIndex": cards_row + 2},
            "properties": {"pixelSize": 46}, "fields": "pixelSize"}},
        {"updateSheetProperties": {
            "properties": {"sheetId": gid, "gridProperties": {
                "frozenRowCount": cards_row + 2}},
            "fields": "gridProperties.frozenRowCount"}},
    ]

    # THE HEADLINE CARDS. Each is two merged columns: a small grey label above
    # a large figure in the colour of what it means. Spent is brand blue,
    # saved is green, still-to-clear is amber — a merchant should be able to
    # read the top of this sheet in one glance and stop there if it wants.
    for i, (_label, value, kind) in enumerate(sheet.cards):
        c0 = i * CARD_SPAN
        strong, wash = CARD_TONE.get(kind, (INK, BAND))
        req += [
            {"mergeCells": {"range": rng(cards_row, cards_row + 1,
                                         c0, c0 + CARD_SPAN),
                            "mergeType": "MERGE_ROWS"}},
            {"mergeCells": {"range": rng(cards_row + 1, cards_row + 2,
                                         c0, c0 + CARD_SPAN),
                            "mergeType": "MERGE_ROWS"}},
            {"repeatCell": {
                "range": rng(cards_row, cards_row + 1, c0, c0 + CARD_SPAN),
                "cell": {"userEnteredFormat": {
                    "backgroundColor": wash,
                    "horizontalAlignment": "LEFT",
                    "verticalAlignment": "MIDDLE",
                    "textFormat": {"fontSize": 9, "bold": True,
                                   "foregroundColor": QUIET}}},
                "fields": "userEnteredFormat(backgroundColor,"
                          "horizontalAlignment,verticalAlignment,textFormat)"}},
            {"repeatCell": {
                "range": rng(cards_row + 1, cards_row + 2, c0, c0 + CARD_SPAN),
                "cell": {"userEnteredFormat": {
                    "backgroundColor": wash,
                    "horizontalAlignment": "LEFT",
                    "verticalAlignment": "MIDDLE",
                    # Every card but a count is money. "Still to clear"
                    # was warn-toned and so fell through to a plain number,
                    # printing 6,695 beside ₹11,570 as if it were a quantity.
                    "numberFormat": ({"type": "NUMBER", "pattern": "#,##0"}
                                     if kind == "count"
                                     else {"type": "CURRENCY",
                                           "pattern": RUPEES_ROUND}),
                    "textFormat": {"fontSize": 18, "bold": True,
                                   "foregroundColor": strong}}},
                "fields": "userEnteredFormat(backgroundColor,"
                          "horizontalAlignment,verticalAlignment,"
                          "numberFormat,textFormat)"}},
        ]

    for spec in layout["blocks"]:
        req += _block_requests(gid, rng, span, spec)

    # column widths come from the widest block that defines each column
    widths = {}
    for b in sheet.blocks:
        for i, w in enumerate(b.widths):
            widths[i] = max(widths.get(i, 0), w)
    for i, w in widths.items():
        if i < span:
            req.append({"updateDimensionProperties": {
                "range": {"sheetId": gid, "dimension": "COLUMNS",
                          "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": w}, "fields": "pixelSize"}})
    return req


def _block_requests(gid, rng, span, spec) -> list:
    b, head = spec["block"], spec["head"]
    first, last, title = spec["first"], spec["last"], spec["title"]
    col = {n: i for i, n in enumerate(b.headings)}
    ncols = len(b.headings)

    req = [
        # a section band, so the eye can find where one table stops
        {"repeatCell": {
            "range": rng(title, title + 1),
            "cell": {"userEnteredFormat": {
                "backgroundColor": BRAND,
                "verticalAlignment": "MIDDLE",
                "textFormat": {"fontSize": 13, "bold": True,
                               "foregroundColor": WHITE}}},
            "fields": "userEnteredFormat(backgroundColor,verticalAlignment,"
                      "textFormat)"}},
        {"mergeCells": {"range": rng(title, title + 1, 0, span),
                        "mergeType": "MERGE_ROWS"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "ROWS",
                      "startIndex": title, "endIndex": title + 1},
            "properties": {"pixelSize": 34}, "fields": "pixelSize"}},
        {"repeatCell": {
            "range": rng(title + 1, title + 2),
            "cell": {"userEnteredFormat": {
                "textFormat": {"fontSize": 9, "italic": True,
                               "foregroundColor": QUIET}}},
            "fields": "userEnteredFormat.textFormat"}},
        {"mergeCells": {"range": rng(title + 1, title + 2, 0, span),
                        "mergeType": "MERGE_ROWS"}},
        {"repeatCell": {
            "range": rng(head, head + 1, 0, ncols),
            "cell": {"userEnteredFormat": {
                "backgroundColor": INK,
                "verticalAlignment": "MIDDLE",
                "textFormat": {"bold": True, "fontSize": 9,
                               "foregroundColor": WHITE}}},
            "fields": "userEnteredFormat(backgroundColor,verticalAlignment,"
                      "textFormat)"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "ROWS",
                      "startIndex": head, "endIndex": head + 1},
            "properties": {"pixelSize": 28}, "fields": "pixelSize"}},
    ]
    if spec["empty"]:
        req.append({"repeatCell": {
            "range": rng(first, last, 0, 1),
            "cell": {"userEnteredFormat": {"textFormat": {
                "italic": True, "foregroundColor": QUIET}}},
            "fields": "userEnteredFormat.textFormat"}})
        return req

    req += [
        {"addBanding": {"bandedRange": {
            "range": rng(head, last, 0, ncols),
            "rowProperties": {"headerColor": INK, "firstBandColor": PAPER,
                              "secondBandColor": BAND}}}},
        {"updateBorders": {"range": rng(head, last, 0, ncols),
                           "innerHorizontal": {"style": "SOLID", "width": 1,
                                               "color": RULE}}},
    ]
    for name in b.money:
        c = col[name]
        req.append({"repeatCell": {
            "range": rng(first, last, c, c + 1),
            "cell": {"userEnteredFormat": {
                "horizontalAlignment": "RIGHT",
                "numberFormat": {"type": "CURRENCY", "pattern": RUPEES}}},
            "fields": "userEnteredFormat(horizontalAlignment,numberFormat)"}})
    for name in b.counts:
        c = col[name]
        req.append({"repeatCell": {
            "range": rng(first, last, c, c + 1),
            "cell": {"userEnteredFormat": {
                "horizontalAlignment": "RIGHT",
                "numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
            "fields": "userEnteredFormat(horizontalAlignment,numberFormat)"}})
    # A saving is the point of the whole thing, so it is green wherever it
    # appears and not merely another number in a row.
    if "You saved" in col:
        c = col["You saved"]
        req.append({"repeatCell": {
            "range": rng(first, last, c, c + 1),
            "cell": {"userEnteredFormat": {"textFormat": {
                "bold": True, "foregroundColor": GOOD}}},
            "fields": "userEnteredFormat.textFormat"}})
    for name in b.ident:
        c = col[name]
        req.append({"repeatCell": {
            "range": rng(first, last, c, c + 1),
            "cell": {"userEnteredFormat": {"textFormat": {
                "fontFamily": "Roboto Mono", "fontSize": 8,
                "foregroundColor": QUIET}}},
            "fields": "userEnteredFormat.textFormat"}})
    for name in b.headings:
        c = col[name]
        req.append({"repeatCell": {
            "range": rng(first, last, c, c + 1),
            "cell": {"userEnteredFormat": {
                "wrapStrategy": "WRAP" if name in b.wrap else "CLIP"}},
            "fields": "userEnteredFormat.wrapStrategy"}})
    for n, (name, contains, tone) in enumerate(b.rules):
        c = col[name]
        req.append({"addConditionalFormatRule": {"index": n, "rule": {
            "ranges": [rng(first, last, c, c + 1)],
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

    Only tabs this program manages are touched.
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
                  out_dir: pathlib.Path | None = None, only: str | None = None):
    """One tab per business, each a dashboard of that business's own trading.

    THREE CALLS, NOT NINETY. Tabs are created in one batch, values written in
    one batch, and formatting applied in one batch, so the cost does not grow
    with the roster.
    """
    import gspread
    from google.oauth2.service_account import Credentials

    from exchange.workbook import merchant_sheet

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(key_file, scopes=scopes)
    book = gspread.authorize(creds).open_by_key(sheet_id)

    roster = [only] if only else merchants(events)[:limit]
    built, owners = {}, {}
    for actor in roster:
        if not entries_for(events, actor).entries:
            continue
        sheet = merchant_sheet(events, actor)
        grid, layout = sheet_grid(sheet)
        built[sheet.key] = (sheet, grid, layout)
        owners[actor] = sheet.key

    existing = {w.title: w for w in book.worksheets()}
    adds = [{"addSheet": {"properties": {
                "title": key,
                "gridProperties": {"rowCount": max(len(grid) + 8, 60),
                                   "columnCount": 14}}}}
            for key, (_s, grid, _l) in built.items() if key not in existing]
    if adds:
        book.batch_update({"requests": adds})
        existing = {w.title: w for w in book.worksheets()}

    # UNMERGE BEFORE WRITING, NOT AFTER. A cell covered by a merge from the
    # previous run silently swallows anything written into it, so with the
    # old layout's merges still in place three columns of every table and two
    # of the headline cards arrived empty — no error, just missing. Clearing
    # the formatting has to happen before the values, not alongside them.
    stale = [existing[t].id for t in LEGACY_TABS
             if t in existing and t not in built]
    reset = [{"deleteSheet": {"sheetId": gid}} for gid in stale]
    reset += _cleanup_requests(book, {existing[k].id for k in built
                                      if k in existing})
    if reset:
        book.batch_update({"requests": reset})

    # Then clear the values, so a shorter run cannot leave last run's rows
    # stranded below the new ones where they read as real records.
    book.values_batch_clear({"ranges": [f"'{k}'" for k in built]})
    book.values_batch_update({
        "valueInputOption": "USER_ENTERED",
        "data": [{"range": f"'{k}'!A1", "values": grid}
                 for k, (_s, grid, _l) in built.items()]})

    fmt = []
    for key, (sheet, grid, layout) in built.items():
        fmt += sheet_requests(existing[key].id, sheet, grid, layout)
    book.batch_update({"requests": fmt})

    tabs = {actor: existing[key].id for actor, key in owners.items()}
    if out_dir is not None:
        # The sheet id itself is NOT written here. It lives in .env, and
        # duplicating a private workbook's identifier into a file that sits
        # beside generated pages is how it ends up somewhere it should not.
        out_dir.mkdir(parents=True, exist_ok=True)
        existing_tabs = {}
        tabs_path = out_dir / TABS_FILE
        if tabs_path.exists():
            try:
                existing_tabs = json.loads(tabs_path.read_text())
            except ValueError:
                existing_tabs = {}
        existing_tabs.update(tabs)
        tabs_path.write_text(
            json.dumps(existing_tabs, indent=2, sort_keys=True),
            encoding="utf-8")
    return list(owners)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Merchant books to CSV or Sheets.")
    parser.add_argument("db", nargs="?", default="runs/market.db")
    parser.add_argument("--out", default="runs/books")
    parser.add_argument("--sheet", action="store_true",
                        help="also push to the Google Sheet in .env")
    parser.add_argument("--only", type=int, default=None,
                        help="push only the first N merchants")
    parser.add_argument("--merchant", default=None,
                        help="push one business only, e.g. m_bl_thirdwave — "
                             "what a demo actually needs")
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
                               out_dir=pathlib.Path(args.out),
                               only=args.merchant)
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
