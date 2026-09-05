"""What a merchant reads in its own books.

Everything here is about the gap between how the exchange stores a figure and
how a person reads it. Prices are paise inside the exchange, ids are keys, and
both are right — until one of them reaches a merchant, at which point it is a
defect. These are the tests that catch that crossing.
"""
from exchange.workbook import merchant_sheet
from tests.test_books import E, _trade


def _flat(sheet) -> str:
    parts = [sheet.title, sheet.key]
    for block in sheet.blocks:
        parts += [str(c) for row in block.rows for c in row]
    return " | ".join(parts)


def _lesson(actor="m_a", partner="m_b"):
    return [E(500, actor, "LESSON_CONSOLIDATED",
              {"counterparty_id": partner,
               "text": "They paid the agreed 19500 per unit and completed "
                       "the settlement."}, "lesson_1")]


def _refusal(corr="turn_1"):
    return [E(300, "gate", "POLICY_DECIDED",
              {"verdict": "DENY",
               "reason": "Amount 3120000 exceeds per-transaction cap 2000000",
               "limits_evaluated": {"amount": 3_120_000}}, corr)]


# --- names, not keys ---------------------------------------------------------

def test_a_business_is_named_the_way_it_trades():
    sheet = merchant_sheet(_trade(), "m_a", {"m_a": "Third Wave Bengaluru",
                                             "m_b": "PowerBank Assemblers"})

    assert sheet.title == "Third Wave Bengaluru"
    assert sheet.key == "Third Wave Bengaluru"
    assert "PowerBank Assemblers" in _flat(sheet)


def test_a_business_the_roster_never_knew_still_reads_as_a_name():
    """One merchant registers live, after the roster was written. Its books
    open the same afternoon and must not greet it with its own actor id."""
    sheet = merchant_sheet(_trade(buyer="m_daybreak"), "m_daybreak")

    assert sheet.title == "Daybreak"
    assert "m_daybreak" not in _flat(sheet)


# --- money, not paise --------------------------------------------------------

def test_the_agents_own_verdict_is_written_in_rupees():
    """The Subconscious files a lesson in the units it was handed. Correct,
    and unreadable: a merchant sees "19500 per unit" for a ₹195 cold brew."""
    sheet = merchant_sheet(_trade() + _lesson(), "m_a")
    text = _flat(sheet)

    assert "₹195 per unit" in text
    assert "19500" not in text


def test_a_refusal_says_what_was_asked_and_what_the_limit_was():
    sheet = merchant_sheet(_trade() + _refusal(), "m_a")
    stopped = next(b for b in sheet.blocks if b.heading == "What the gate stopped")

    assert stopped.rows, "the refusal never reached the merchant's books"
    on, asked, why, next_ = stopped.rows[0]
    assert asked == 31200.0          # a number, for the money format
    assert why == ("₹31,200 is over the cap on a single payment of "
                   "₹20,000")
    assert next_.startswith("Retried smaller and settled at ₹")


def test_an_agent_names_a_counterparty_the_way_a_person_would():
    """The agent writes "m_reelco paid the full ..." because that is how it
    holds the counterparty. The figures in that sentence were cleaned up long
    before the name beside them was."""
    lesson = [E(500, "m_a", "LESSON_CONSOLIDATED",
                {"counterparty_id": "m_b",
                 "text": "m_b paid the full 496800 and honoured the terms."},
                "lesson_1")]
    sheet = merchant_sheet(_trade() + lesson, "m_a", {"m_b": "ReelCo Paper"})
    text = _flat(sheet)

    assert "ReelCo Paper paid the full ₹4,968" in text
    assert "m_b " not in text


def test_no_bare_paise_survives_anywhere_on_the_sheet():
    """The blanket check. Every figure a merchant reads is either formatted
    money or a quantity, and the paise the log stores reach neither."""
    sheet = merchant_sheet(_trade() + _lesson() + _refusal(), "m_a")

    assert "3120000" not in _flat(sheet)
    assert "2000000" not in _flat(sheet)


def test_a_figure_already_in_rupees_is_not_divided_twice():
    """The rail formats a price, then the humanise pass ran over it again:
    "agreed ₹195 a unit" came back as "agreed ₹₹1.95 a unit"."""
    from exchange.plain import money_words

    assert money_words("agreed ₹195 a unit") == "agreed ₹195 a unit"
    assert money_words("paid ₹4,875 today") == "paid ₹4,875 today"
    # and the conversion still happens where the figure really is paise
    assert money_words("agreed 19500 per unit") == "agreed ₹195 per unit"
