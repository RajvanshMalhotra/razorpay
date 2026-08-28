"""The books a merchant is owed.

An agent that spends someone's money owes them a ledger, and a ledger is only
worth anything if it is complete and honest about what has not cleared yet.
Both of those are what these tests are about.
"""
from exchange.books import COLUMNS, entries_for, sheet_rows


class E:
    """A log event, shaped the way the reader sees them."""

    def __init__(self, seq, actor, type_, payload, corr, ts="2026-08-24T10:00:00Z"):
        self.seq = seq
        self.actor_id = actor
        self.type = type_
        self.payload = payload
        self.correlation_id = corr
        self.ts = ts


def _trade(corr="turn_1", buyer="m_a", seller="m_b", amount=490_000,
           qty=20, done=True, by="m_a", verdicts=("ALLOW",), base=100,
           agreed=24_500, clearing=None, ask="ord_ask"):
    events = [
        E(base, seller, "ORDER_POSTED",
          {"order_id": ask, "side": "ASK", "asset_ref": "ast_1"}, "seed"),
        E(base + 1, buyer, "ORDER_POSTED",
          {"order_id": "ord_bid", "side": "BID",
           "asset_query": {"text": "cold brew concentrate"}}, corr),
        E(base + 2, buyer, "COUNTERPARTY_CHOSEN",
          {"ask_order_id": ask}, corr),
        E(base + 3, buyer, "MATCH_PROPOSED",
          {"ask_order_id": ask, "qty": qty,
           "clearing_price": clearing}, corr),
    ]
    if agreed is not None:
        events.append(E(base + 9, buyer, "NEGOTIATION_ENDED",
                        {"agreed": True, "final_price": agreed}, corr))
    events += [E(base + 4 + n, "gate", "POLICY_DECIDED", {"verdict": v}, corr)
               for n, v in enumerate(verdicts)]
    events.append(E(base + 10, buyer, "SETTLEMENT_INITIATED",
                    {"settlement_id": "s1", "currency": "INR",
                     "amount": amount,
                     "razorpay_order_id": "order_X"}, corr))
    if done:
        events.append(E(base + 11, by, "SETTLEMENT_COMPLETED",
                        {"settlement_id": "s1",
                         "razorpay_payment_id": "pay_X"}, corr))
    return events


# --- both sides ---------------------------------------------------------------

def test_a_buyer_sees_the_trade_as_a_purchase():
    books = entries_for(_trade(), "m_a")

    assert [e.direction for e in books.entries] == ["bought"]
    assert books.entries[0].counterparty == "m_b"
    assert books.bought_inr == 4900.0


def test_the_seller_sees_the_same_trade_as_a_sale():
    """Reporting only purchases would show a business that spends and never
    earns, which is the wrong shape for a ledger entirely."""
    books = entries_for(_trade(), "m_b")

    assert [e.direction for e in books.entries] == ["sold"]
    assert books.entries[0].counterparty == "m_a"
    assert books.sold_inr == 4900.0


def test_net_is_sales_less_purchases():
    events = _trade(corr="turn_1", buyer="m_a", seller="m_b", amount=100_000)
    events += _trade(corr="turn_2", buyer="m_c", seller="m_a",
                     amount=300_000, base=200)

    books = entries_for(events, "m_a")

    assert books.bought_inr == 1000.0
    assert books.sold_inr == 3000.0
    assert books.net_inr == 2000.0


def test_a_merchant_uninvolved_in_a_trade_never_sees_it():
    """A merchant's books must contain that merchant's business and nothing
    else — the same boundary the privacy floor enforces upstream."""
    assert entries_for(_trade(), "m_stranger").entries == []


# --- pending is not paid ------------------------------------------------------

def test_an_unconfirmed_settlement_is_pending_not_settled():
    """The whole reason this project has an accountant is that committed and
    confirmed are different numbers."""
    books = entries_for(_trade(done=False), "m_a")

    assert books.entries[0].status == "pending"
    assert books.entries[0].razorpay_payment_id == ""
    assert books.bought_inr == 4900.0
    assert books.settled_inr == 0.0


def test_a_repaired_settlement_is_marked_as_repaired():
    """It counts as money, and it does not pretend the repair never happened."""
    books = entries_for(_trade(by="accountant"), "m_a")

    assert books.entries[0].status == "repaired"
    assert books.settled_inr == 4900.0


def test_the_payment_id_comes_from_the_completion_not_from_us():
    books = entries_for(_trade(), "m_a")

    assert books.entries[0].razorpay_payment_id == "pay_X"


# --- what the columns say -----------------------------------------------------

def test_the_unit_price_is_the_price_that_was_agreed():
    books = entries_for(_trade(amount=490_000, agreed=24_500), "m_a")

    assert books.entries[0].unit_price_inr == 245.0
    assert books.entries[0].qty == 20


def test_a_capped_trade_reports_the_agreed_price_not_the_full_lot():
    """THE BUG THIS CAUGHT. The gate capped a trade to a trial size, the
    match still named the full lot of 160, and dividing the amount by that
    gave 30.47 per unit for a deal agreed at 195. The price and the amount
    are what the settlement is accountable to; the quantity follows from
    them, so qty x unit always equals the amount."""
    books = entries_for(
        _trade(amount=487_500, qty=160, agreed=19_500), "m_a")
    entry = books.entries[0]

    assert entry.unit_price_inr == 195.0
    assert entry.qty == 25
    assert round(entry.qty * entry.unit_price_inr, 2) == entry.amount_inr


def test_a_trade_with_no_agreed_price_falls_back_to_the_clearing_price():
    books = entries_for(_trade(amount=100_000, agreed=None, clearing=5_000),
                        "m_a")

    assert books.entries[0].unit_price_inr == 50.0


def test_a_trade_with_no_price_at_all_leaves_the_columns_empty():
    """Better an empty cell than a division by zero dressed as a price."""
    books = entries_for(_trade(agreed=None, clearing=None), "m_a")

    assert books.entries[0].unit_price_inr is None
    assert books.entries[0].qty is None
    assert books.entries[0].row()[COLUMNS.index("unit_price_inr")] == ""


def test_a_refusal_is_recorded_beside_the_trade_it_bound():
    """A merchant should be able to see, in its own books, that its agent was
    told no once and settled smaller."""
    books = entries_for(_trade(verdicts=("DENY", "ALLOW")), "m_a")

    assert books.entries[0].gate == "DENY then ALLOW"


def test_points_trades_stay_out_of_the_rupee_books():
    """Points are not money and must never land in a rupee column."""
    events = _trade()
    events.append(E(300, "m_a", "SETTLEMENT_INITIATED",
                    {"settlement_id": "s2", "currency": "CREDITS",
                     "amount": 1200}, "turn_9"))

    assert len(entries_for(events, "m_a").entries) == 1


def test_entries_are_ordered_by_when_they_happened():
    events = _trade(corr="turn_2", base=300) + _trade(corr="turn_1", base=100)

    seqs = [e.event for e in entries_for(events, "m_a").entries]

    assert seqs == sorted(seqs)


# --- the sheet ----------------------------------------------------------------

def test_the_sheet_puts_the_totals_before_the_detail():
    grid = sheet_rows(entries_for(_trade(), "m_a"))

    assert grid[0][0] == "Merchant"
    assert list(COLUMNS) in grid
    assert grid.index(list(COLUMNS)) > 0


def test_every_ledger_row_has_one_cell_per_column():
    """A short row silently shifts every value after it into the wrong
    column, which is the worst possible failure for a book of accounts."""
    grid = sheet_rows(entries_for(_trade(), "m_a"))
    header = grid.index(list(COLUMNS))

    for row in grid[header + 1:]:
        assert len(row) == len(COLUMNS)


def test_a_merchant_with_no_trades_still_produces_a_readable_sheet():
    grid = sheet_rows(entries_for(_trade(), "m_nobody"))

    assert grid[0] == ["Merchant", "m_nobody"]
    assert list(COLUMNS) in grid
