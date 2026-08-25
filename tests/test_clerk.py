"""The clerk decides what gets paid, so its worst bug is paying twice.

In test mode a second payment is as real as the first: it captures, it lands
in the books, and it makes the accountant's totals wrong in a way that reads
like a conservation bug rather than an operator error.
"""
import pytest

from exchange.eventlog import EventLog
from exchange.events import (
    SETTLEMENT_COMPLETED,
    SETTLEMENT_FAILED,
    SETTLEMENT_INITIATED,
)
from scripts.market.clerk import pending_payments


@pytest.fixture
def log(tmp_path):
    lg = EventLog(str(tmp_path / "clerk.db"))
    yield lg
    lg.close()


def _initiated(log, sid, *, link="plink_1", url="https://rzp.io/rzp/AAA",
               amount=970_000, corr="c", actor="m_buyer", currency="INR",
               link_error=None):
    payload = {
        "settlement_id": sid, "match_id": "mch", "currency": currency,
        "amount": amount, "razorpay_order_id": "order_receipt",
        "payment_link_id": link, "payment_link_url": url,
    }
    if link_error is not None:
        payload["payment_link_error"] = link_error
    log.append(actor, SETTLEMENT_INITIATED, payload, correlation_id=corr)


def test_an_unpaid_settlement_is_payable(log):
    _initiated(log, "stl_1")

    report = pending_payments(log)

    assert len(report.payable) == 1
    assert report.payable[0].settlement_id == "stl_1"
    assert report.payable[0].payment_link_id == "plink_1"
    assert report.payable[0].amount == 970_000
    assert report.outstanding == 970_000


def test_a_completed_settlement_is_never_offered_again(log):
    """The one that costs real money if it is wrong."""
    _initiated(log, "stl_1")
    log.append("m_buyer", SETTLEMENT_COMPLETED,
               {"settlement_id": "stl_1", "razorpay_payment_id": "pay_1"},
               correlation_id="c")

    report = pending_payments(log)

    assert report.payable == ()
    assert report.outstanding == 0


def test_a_failed_settlement_is_not_offered(log):
    """It did not just go unpaid; it resolved. Paying it now would settle a
    trade the exchange has already written off."""
    _initiated(log, "stl_1")
    log.append("m_buyer", SETTLEMENT_FAILED,
               {"settlement_id": "stl_1", "match_id": "mch", "currency": "INR",
                "amount": 970_000, "reason": "razorpay unreachable"},
               correlation_id="c")

    assert pending_payments(log).payable == ()


def test_a_settlement_with_no_link_is_reported_as_unpayable(log):
    """Not silently dropped. No amount of clicking will fix it, and the
    post-mortem has to tell it apart from 'nobody got round to it'."""
    _initiated(log, "stl_1", link=None, url=None,
               link_error="RuntimeError: payment link service unavailable")

    report = pending_payments(log)

    assert report.payable == ()
    assert len(report.unpayable) == 1
    assert "payment link service unavailable" in report.unpayable[0].reason


def test_a_points_settlement_has_nothing_to_pay(log):
    """Points move by ledger transfer. Offering one for payment would send
    rupees for a trade that was never denominated in them."""
    _initiated(log, "stl_1", currency="CREDITS", link=None, url=None)

    report = pending_payments(log)

    assert report.payable == ()
    assert report.unpayable == ()


def test_the_clerk_reads_the_link_not_the_order(log):
    """The order recorded at settlement time cannot receive a payment: the
    payable order is minted by the link when someone pays it."""
    _initiated(log, "stl_1")

    payable = pending_payments(log).payable[0]

    assert payable.payment_link_id == "plink_1"
    assert payable.payment_link_url.startswith("https://")


def test_the_clerk_carries_the_trades_correlation_id(log):
    """So a paid settlement can be followed back into its own story."""
    _initiated(log, "stl_1", corr="corr_the_trade")

    assert pending_payments(log).payable[0].correlation_id == "corr_the_trade"


def test_many_settlements_are_reported_in_log_order(log):
    _initiated(log, "stl_1", amount=100)
    _initiated(log, "stl_2", amount=200, link="plink_2")
    _initiated(log, "stl_3", amount=300, link="plink_3")
    log.append("m_buyer", SETTLEMENT_COMPLETED,
               {"settlement_id": "stl_2", "razorpay_payment_id": "p"},
               correlation_id="c")

    report = pending_payments(log)

    assert [p.settlement_id for p in report.payable] == ["stl_1", "stl_3"]
    assert report.outstanding == 400


def test_a_duplicated_initiation_is_only_offered_once(log):
    """An append-only log can carry a repeat; paying it twice is real money."""
    _initiated(log, "stl_1")
    _initiated(log, "stl_1")

    assert len(pending_payments(log).payable) == 1


def test_an_empty_log_has_nothing_to_pay(log):
    report = pending_payments(log)

    assert report.payable == ()
    assert report.unpayable == ()
    assert report.outstanding == 0
