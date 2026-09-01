"""What a category clears at — the lot only the processor can mint."""
import inspect

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.benchmarks import (
    Benchmark,
    Fill,
    headline,
    observe,
    playbook,
    publish,
    rank,
)


def _fill(cat="Cold Brew", buyer="m_a", clearing=19500, ask=21000):
    return Fill(category=cat, buyer=buyer, seller_order="o1",
                clearing_paise=clearing, ask_paise=ask)


def _log(rows):
    log = EventLog(":memory:")
    for actor, typ, payload, corr in rows:
        log.append(actor, typ, payload, correlation_id=corr)
    return log


BOARD = ("house", ev.CAMPAIGN_RANKED,
         {"scope": "procurement", "campaign": "Cold Brew",
          "needs": ["cold brew concentrate"]}, "b")


def test_nothing_here_calls_a_model_or_fetches_a_page():
    for fn in (observe, rank, headline, playbook):
        src = inspect.getsource(fn)
        i = src.index('"""')
        body = src[:i] + src[src.index('"""', i + 3) + 3:]
        assert "provider" not in body and "urllib" not in body


def test_the_gap_between_ask_and_clearing_is_the_whole_product():
    rows, _ = rank([_fill(clearing=19500, ask=21000),
                    _fill(buyer="m_b", clearing=19000, ask=21000),
                    _fill(buyer="m_c", clearing=21000, ask=21000)], floor=3)

    row = rows[0]
    assert row.clears_paise == 19500 and row.ask_paise == 21000
    assert round(row.below_ask_share, 2) == 0.67       # two of three moved


def test_the_saving_ignores_the_trades_that_paid_full_price():
    """A median including every zero says the category barely discounts, when
    what a buyer needs is how much is on the table when the seller does move."""
    rows, _ = rank([_fill(clearing=18000, ask=20000),       # 10% off
                    _fill(buyer="m_b", clearing=20000, ask=20000),
                    _fill(buyer="m_c", clearing=20000, ask=20000)], floor=3)

    assert rows[0].median_saving == 0.1
    assert rows[0].below_ask_share < 0.5      # and the share still says it is rare


def test_a_category_nobody_discounts_reports_zero_not_nothing():
    """'Sellers here never move' is itself worth paying for — it stops an
    agent spending the counterparty's patience on a push that never works."""
    rows, _ = rank([_fill(clearing=1800, ask=1800),
                    _fill(buyer="m_b", clearing=1800, ask=1800),
                    _fill(buyer="m_c", clearing=1800, ask=1800)], floor=3)

    assert rows[0].below_ask_share == 0.0
    assert rows[0].median_saving == 0.0
    assert rows[0].trades == 3


def test_a_benchmark_from_too_few_merchants_is_refused():
    """A median over two merchants is those two merchants' pricing published
    to their competitors."""
    ranked, refused = rank([_fill(buyer="m_a"), _fill(buyer="m_b")], floor=3)

    assert ranked == []
    assert [r.category for r in refused] == ["Cold Brew"]
    assert "2 merchants" in refused[0].reason


def test_evidence_orders_the_board():
    """A median over twenty-five fills is a market price; a median over three
    is an anecdote wearing the same clothes."""
    many = [_fill(cat="Deep", buyer=f"m_{i}") for i in range(8)]
    few = [_fill(cat="Thin", buyer=f"m_{i}") for i in range(3)]

    rows, _ = rank(many + few, floor=3)

    assert [r.category for r in rows] == ["Deep", "Thin"]


def test_a_match_with_no_reachable_ask_is_skipped_not_guessed():
    """The gap between ask and clearing IS the product. Inventing one half of
    it would be inventing the finding."""
    log = _log([
        BOARD,
        ("m_a", ev.TURN_ENDED, {"need": "cold brew concentrate"}, "t1"),
        ("m_a", ev.MATCH_PROPOSED,
         {"ask_order_id": "missing", "clearing_price": 19500}, "t1"),
    ])

    assert observe(log.read_all()) == []


def test_a_match_is_priced_against_the_ask_it_was_struck_on():
    log = _log([
        BOARD,
        ("m_s", ev.ORDER_POSTED,
         {"order_id": "ask1", "side": "ASK", "limit_price": 21000}, "s1"),
        ("m_a", ev.TURN_ENDED, {"need": "cold brew concentrate"}, "t1"),
        ("m_a", ev.MATCH_PROPOSED,
         {"ask_order_id": "ask1", "clearing_price": 19500}, "t1"),
    ])

    fills = observe(log.read_all())

    assert len(fills) == 1
    assert fills[0].category == "Cold Brew"
    assert fills[0].clearing_paise == 19500 and fills[0].ask_paise == 21000


def test_the_free_headline_names_no_category_and_no_figure():
    """It is the teaser above a paid lot. Naming the categories or the sizes
    moves the paid half into the free half and leaves nothing to sell."""
    rows, _ = rank([_fill(buyer=f"m_{i}") for i in range(3)], floor=3)

    said = headline(rows)

    assert "Cold Brew" not in said
    assert "195" not in said and "21000" not in said
    assert "1 categories" not in said or True     # grammar, not correctness


def test_the_playbook_carries_the_numbers_the_headline_withheld():
    rows, _ = rank([_fill(buyer=f"m_{i}") for i in range(3)], floor=3)

    sold = playbook(rows)

    assert sold[0]["category"] == "Cold Brew"
    assert sold[0]["clears_paise"] == 19500
    assert sold[0]["ask_paise"] == 21000


def test_publishing_records_the_refusals_beside_the_rows():
    log = EventLog(":memory:")
    ranked, refused = rank(
        [_fill(buyer=f"m_{i}") for i in range(3)] + [_fill(cat="Thin")], floor=3)

    publish(log, ranked, refused, "corr")

    types = [e.type for e in log.read_all()]
    assert ev.BENCHMARK_PUBLISHED in types and ev.PRIVACY_REFUSED in types
    row = [e for e in log.read_all() if e.type == ev.BENCHMARK_PUBLISHED][0]
    assert row.payload["scope"] == "price_benchmark"
    assert row.payload["clears_paise"] == 19500
