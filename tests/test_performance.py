"""Did the campaign convert? Arithmetic over settlements, never scraped."""
import inspect

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.performance import (
    Attempt,
    observe,
    publish,
    rank,
)


def _a(campaign, amount=1000, link=True, captured=True, refused=False,
       seconds=None, corr="c"):
    return Attempt(campaign=campaign, correlation_id=corr, amount=amount,
                   link_issued=link, captured=captured, refused=refused,
                   seconds_to_pay=seconds)


def _log_with(events):
    log = EventLog(":memory:")
    for actor, typ, payload, corr in events:
        log.append(actor, typ, payload, correlation_id=corr)
    return log


BOARD = ("house", ev.CAMPAIGN_RANKED,
         {"scope": "procurement", "rank": 1, "campaign": "Cold Brew",
          "needs": ["cold brew concentrate", "arabica beans"]}, "board")


# --- the load-bearing separation ---------------------------------------------

def test_nothing_here_calls_a_model_or_fetches_a_page():
    """This board's whole claim is that it costs nothing and depends on no
    third party. One fetch would make that false."""
    for fn in (observe, rank):
        source = inspect.getsource(fn)
        start = source.index('"""')
        body = source[:start] + source[source.index('"""', start + 3) + 3:]
        assert "provider" not in body and "complete(" not in body
        assert "urllib" not in body and "http" not in body.lower()


# --- conversion --------------------------------------------------------------

def test_conversion_is_paid_over_asked():
    rows = rank([_a("A"), _a("A", captured=False), _a("A"), _a("A")])

    assert rows[0].links == 4 and rows[0].paid == 3
    assert rows[0].conversion == 0.75


def test_a_campaign_nobody_was_asked_to_pay_for_converted_nothing():
    """Zero links must not be a division by zero, and must never be 100% —
    that would be the most flattering possible lie."""
    rows = rank([_a("A", link=False, captured=False),
                 _a("A", link=False, captured=False)])

    assert rows[0].links == 0
    assert rows[0].conversion == 0.0


def test_revenue_counts_only_what_was_captured():
    """A link that was issued and never paid is not revenue, and a board that
    counted it would be reporting money that does not exist."""
    rows = rank([_a("A", amount=5000), _a("A", amount=9000, captured=False)])

    assert rows[0].revenue_paise == 5000
    assert rows[0].aov_paise == 5000


def test_ranking_puts_earnings_above_a_flattering_rate():
    """A campaign converting perfectly on two small orders has not beaten one
    converting two thirds of forty large ones."""
    small = [_a("Small", amount=1000) for _ in range(2)]
    big = ([_a("Big", amount=50_000) for _ in range(4)]
           + [_a("Big", amount=50_000, captured=False) for _ in range(2)])

    rows = rank(small + big)

    assert [r.campaign for r in rows] == ["Big", "Small"]
    assert rows[1].conversion == 1.0        # and it still lost


def test_the_median_ignores_the_ones_that_never_paid():
    rows = rank([_a("A", seconds=60), _a("A", seconds=600),
                 _a("A", captured=False, seconds=None)])

    assert rows[0].median_seconds == 600


# --- joining -----------------------------------------------------------------

def test_a_turn_is_followed_through_to_whether_the_money_landed():
    log = _log_with([
        BOARD,
        ("m_a", ev.TURN_ENDED, {"need": "cold brew concentrate"}, "t1"),
        ("m_a", ev.SETTLEMENT_INITIATED,
         {"amount": 4875, "settlement_id": "s1", "payment_link_id": "pl1"}, "t1"),
        ("m_a", ev.SETTLEMENT_COMPLETED, {"settlement_id": "s1"}, "t1"),
    ])

    attempts, unmatched = observe(log.read_all())

    assert unmatched == 0
    assert len(attempts) == 1
    assert attempts[0].campaign == "Cold Brew"
    assert attempts[0].link_issued and attempts[0].captured
    assert attempts[0].amount == 4875


def test_a_turn_on_no_published_row_is_counted_not_dropped():
    """A board that quietly measures two thirds of the trading looks exactly
    like a board that measured all of it."""
    log = _log_with([
        BOARD,
        ("m_a", ev.TURN_ENDED, {"need": "cold brew concentrate"}, "t1"),
        ("m_b", ev.TURN_ENDED, {"need": "something nobody ranked"}, "t2"),
    ])

    attempts, unmatched = observe(log.read_all())

    assert len(attempts) == 1
    assert unmatched == 1


def test_the_gate_refusals_are_carried_onto_the_row():
    log = _log_with([
        BOARD,
        ("m_a", ev.TURN_ENDED, {"need": "cold brew concentrate"}, "t1"),
        ("gate", ev.POLICY_DECIDED, {"verdict": "DENY", "reason": "over cap"}, "t1"),
        ("m_a", ev.SETTLEMENT_INITIATED,
         {"amount": 4875, "settlement_id": "s1", "payment_link_id": "pl1"}, "t1"),
        ("m_a", ev.SETTLEMENT_COMPLETED, {"settlement_id": "s1"}, "t1"),
    ])

    attempts, _ = observe(log.read_all())

    assert attempts[0].refused is True
    assert rank(attempts)[0].stopped == 1


def test_an_allow_is_not_counted_as_a_refusal():
    log = _log_with([
        BOARD,
        ("m_a", ev.TURN_ENDED, {"need": "cold brew concentrate"}, "t1"),
        ("gate", ev.POLICY_DECIDED, {"verdict": "ALLOW"}, "t1"),
    ])

    attempts, _ = observe(log.read_all())

    assert attempts[0].refused is False


def test_the_radar_rows_are_not_a_source_of_campaigns():
    """The radar counts strangers talking about other companies' campaigns.
    Joining a Razorpay settlement to one would be meaningless."""
    log = _log_with([
        ("house", ev.CAMPAIGN_RANKED,
         {"scope": "brand_radar", "rank": 1, "campaign": "Instagram rebrand"},
         "radar"),
        ("m_a", ev.TURN_ENDED, {"need": "cold brew concentrate"}, "t1"),
    ])

    attempts, unmatched = observe(log.read_all())

    assert attempts == [] and unmatched == 0     # no board at all, so nothing


def test_no_board_published_means_no_performance_claimed():
    log = _log_with([("m_a", ev.TURN_ENDED, {"need": "anything"}, "t1")])

    assert observe(log.read_all()) == ([], 0)


# --- publishing --------------------------------------------------------------

def test_every_row_says_how_much_trading_it_speaks_for():
    log = EventLog(":memory:")
    publish(log, rank([_a("A", amount=5000, seconds=120)]), 7, "corr")

    row = log.read_all()[0].payload
    assert row["campaign"] == "A"
    assert row["conversion"] == 1.0
    assert row["revenue_paise"] == 5000
    assert row["median_seconds_to_pay"] == 120
    assert row["unmatched_turns"] == 7
    assert row["scope"] == "performance"
