"""The graded moment: a failure the system catches and repairs by itself.

Tested against a client shaped like live test mode — the receipt order never
holds a payment, and the capture is visible only through the link. A kinder
fake is what let `repair` keep polling an unpayable order through four review
passes.
"""
import pytest

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.models import ActorStatus
from exchange.projections import fold
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex
from exchange.service import Exchange
from scripts.market.inject_failure import a_settlement_to_break, handle_drift
from tests.test_rails import FakeRazorpay
from tests.test_retrieval import fake_embedder


class PaidAtRazorpay:
    """The capture happened upstream; the receipt order shows nothing."""

    def __init__(self, payment_id="pay_recovered", captured=True):
        self._payment_id = payment_id
        self._captured = captured
        outer = self

        class _Orders:
            @staticmethod
            def payments(order_id):
                return {"count": 0, "items": []}

        class _Links:
            @staticmethod
            def fetch(link_id):
                if not outer._captured:
                    return {"id": link_id, "status": "created"}
                return {"id": link_id, "status": "paid",
                        "order_id": "order_minted_by_link",
                        "payments": [{"payment_id": outer._payment_id,
                                      "status": "captured"}]}

        self.order = _Orders()
        self.payment_link = _Links()


@pytest.fixture
def market(tmp_path):
    log = EventLog(str(tmp_path / "fail.db"))
    exchange = Exchange(
        log, HybridIndex(embed_fn=fake_embedder),
        RazorpayRail(log, FakeRazorpay()), CreditRail(log),
    )
    yield exchange
    log.close()


def _pending_trade(exchange, sid="stl_1", corr="corr_the_trade", actor="m_a"):
    exchange.log.append(actor, ev.ACTOR_REGISTERED,
                        {"actor_id": actor, "kind": "MERCHANT"},
                        correlation_id="reg")
    exchange.log.append(actor, ev.SETTLEMENT_INITIATED, {
        "settlement_id": sid, "match_id": "mch_1", "currency": "INR",
        "amount": 970_000, "razorpay_order_id": "order_receipt",
        "payment_link_id": "plink_1",
        "payment_link_url": "https://rzp.io/rzp/AAA",
    }, correlation_id=corr)


def test_the_whole_arc_reads_on_the_trades_own_thread(market):
    """Pin the trade and the story is complete, and in the right order.

    Before the drift was moved onto the trade's correlation, this read
    INITIATED -> COMPLETED: a settlement that mysteriously fixed itself, with
    every interesting part filed somewhere a reader would never look.
    """
    _pending_trade(market)

    report = handle_drift(market, PaidAtRazorpay())

    assert report.complete
    assert report.story == (
        "SETTLEMENT_INITIATED",
        "DRIFT_DETECTED",
        "ACTOR_FROZEN",
        "SETTLEMENT_COMPLETED",
        "ACTOR_RESUMED",
    )


def test_the_repair_records_razorpays_payment_id(market):
    """The remote is the authority on whether money moved. A repair that
    invents an id is a machine for asserting payments that never happened."""
    _pending_trade(market)

    report = handle_drift(market, PaidAtRazorpay(payment_id="pay_from_razorpay"))

    assert report.repaired_payment_id == "pay_from_razorpay"


def test_the_capture_is_found_through_the_link(market):
    """The receipt order returns count 0 forever; only the link knows."""
    _pending_trade(market)
    client = PaidAtRazorpay()

    assert handle_drift(market, client).complete


def test_the_freeze_lands_before_the_resume(market):
    """That ordering IS the demo. Present but out of order and the story
    does not read."""
    _pending_trade(market)

    report = handle_drift(market, PaidAtRazorpay())

    assert report.story.index("ACTOR_FROZEN") < report.story.index("ACTOR_RESUMED")


def test_the_merchant_ends_active(market):
    """A freeze that never lifts is a ban, not a hold."""
    _pending_trade(market)

    handle_drift(market, PaidAtRazorpay())

    assert fold(market.log.read_all()).actors["m_a"].status == ActorStatus.ACTIVE


def test_a_settlement_that_did_not_drift_is_not_repaired(market):
    """No captured payment means the remote does not agree money moved.
    Injecting a 'failure' here would be inventing one."""
    _pending_trade(market)

    report = handle_drift(market, PaidAtRazorpay(captured=False))

    assert not report.drift_found
    assert report.reason and "has not drifted" in report.reason
    assert not any(e.type == ev.SETTLEMENT_COMPLETED
                   for e in market.log.read_all())


def test_an_already_completed_settlement_is_never_chosen(market):
    """Choosing one would produce a 'failure' that is really a double
    repair — and a second completion for one payment."""
    _pending_trade(market)
    market.log.append("m_a", ev.SETTLEMENT_COMPLETED,
                      {"settlement_id": "stl_1", "razorpay_payment_id": "pay_x"},
                      correlation_id="corr_the_trade")

    assert a_settlement_to_break(market.log) is None


def test_a_points_settlement_is_never_chosen(market):
    """Points move by ledger transfer; there is no remote to disagree with."""
    market.log.append("m_a", ev.SETTLEMENT_INITIATED, {
        "settlement_id": "stl_c", "match_id": "m", "currency": "CREDITS",
        "amount": 1200,
    }, correlation_id="c")

    assert a_settlement_to_break(market.log) is None


def test_nothing_to_break_is_reported_not_raised(market):
    """The operator's mistake is running this before trading, and it should
    say so rather than stack-trace."""
    report = handle_drift(market, PaidAtRazorpay())

    assert not report.complete
    assert report.reason and "trade first" in report.reason


def test_the_accountant_is_given_no_hint(market):
    """It reconciles the whole book and finds the drift on its own. Telling
    it which settlement to care about would make this a puppet show."""
    _pending_trade(market, sid="stl_1", corr="corr_one")
    _pending_trade(market, sid="stl_2", corr="corr_two", actor="m_b")

    report = handle_drift(market, PaidAtRazorpay())

    drifts = [e for e in market.log.read_all() if e.type == "DRIFT_DETECTED"]
    assert len(drifts) == 2, "reconcile examined the whole book, not one row"
    assert report.settlement_id == "stl_1"


def test_only_the_drifting_merchant_is_frozen(market):
    """Per-actor, never global: one merchant's disagreement must not halt
    the market."""
    _pending_trade(market, sid="stl_1", corr="corr_one", actor="m_a")
    market.log.append("m_b", ev.ACTOR_REGISTERED,
                      {"actor_id": "m_b", "kind": "MERCHANT"},
                      correlation_id="reg_b")

    handle_drift(market, PaidAtRazorpay(), settlement_id="stl_1")

    actors = fold(market.log.read_all()).actors
    assert actors["m_b"].status == ActorStatus.ACTIVE
