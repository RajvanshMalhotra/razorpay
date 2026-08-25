import pytest

from exchange.eventlog import EventLog
from exchange.events import (
    CREDITS_TRANSFERRED,
    SETTLEMENT_COMPLETED,
    SETTLEMENT_FAILED,
    SETTLEMENT_INITIATED,
)
from exchange.models import SettlementStatus
from exchange.projections import fold
from exchange.rails.base import InsufficientCredits
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail


@pytest.fixture
def log(tmp_path):
    lg = EventLog(str(tmp_path / "rails.db"))
    yield lg
    lg.close()


class FakeRazorpay:
    """Stands in for razorpay.Client. `payments_by_order` drives the outcome."""

    def __init__(self, payments_by_order=None, fail_on_create=False,
                 payment_link_url="https://rzp.io/rzp/FAKE123", fail_payment_link=False):
        self._payments = payments_by_order or {}
        self._fail = fail_on_create
        self._link_url = payment_link_url
        self._fail_link = fail_payment_link
        self.created = []
        self.order = self._Orders(self)
        self.payment_link = self._PaymentLinks(self)

    class _Orders:
        def __init__(self, outer):
            self._outer = outer

        def create(self, data):
            if self._outer._fail:
                raise RuntimeError("razorpay unreachable")
            order_id = f"order_{len(self._outer.created) + 1}"
            self._outer.created.append(data)
            return {"id": order_id, "amount": data["amount"], "status": "created"}

        def payments(self, order_id):
            return self._outer._payments.get(order_id, {"count": 0, "items": []})

    class _PaymentLinks:
        def __init__(self, outer):
            self._outer = outer

        def create(self, data):
            if self._outer._fail_link:
                raise RuntimeError("payment link service unavailable")
            return {"id": "plink_fake", "short_url": self._outer._link_url}


# --- credits rail -----------------------------------------------------------

def test_credit_transfer_completes_and_moves_balances(log):
    log.append("house", CREDITS_TRANSFERRED,
               {"from_actor_id": "house", "to_actor_id": "m_a", "amount": 5000},
               correlation_id="seed")
    rail = CreditRail(log)

    settlement = rail.settle("mch_1", "m_a", "m_b", 1200, correlation_id="c1")

    assert settlement.status == SettlementStatus.COMPLETED
    state = fold(log.read_all())
    assert state.credit_balances["m_a"] == 3800
    assert state.credit_balances["m_b"] == 1200


def test_credit_transfer_is_refused_when_the_balance_is_short(log):
    log.append("house", CREDITS_TRANSFERRED,
               {"from_actor_id": "house", "to_actor_id": "m_a", "amount": 100},
               correlation_id="seed")
    rail = CreditRail(log)

    with pytest.raises(InsufficientCredits):
        rail.settle("mch_1", "m_a", "m_b", 1200, correlation_id="c1")


def test_refused_credit_transfer_writes_no_transfer_event(log):
    rail = CreditRail(log)

    with pytest.raises(InsufficientCredits):
        rail.settle("mch_1", "m_a", "m_b", 1200, correlation_id="c1")

    types = [e.type for e in log.read_by_correlation("c1")]
    assert CREDITS_TRANSFERRED not in types


def test_refused_credit_transfer_records_the_failure_before_raising(log):
    """The gate has already written ALLOW. An ALLOW that resolves to nothing is
    the hole a reconciler cannot see through, so the outcome must be logged."""
    rail = CreditRail(log)

    with pytest.raises(InsufficientCredits):
        rail.settle("mch_1", "m_a", "m_b", 1200, correlation_id="c1")

    failed = [e for e in log.read_by_correlation("c1") if e.type == SETTLEMENT_FAILED]
    assert len(failed) == 1
    assert failed[0].payload["match_id"] == "mch_1"
    assert failed[0].payload["reason"]
    assert "1200" in failed[0].payload["reason"]


def test_a_refused_credit_settlement_folds_to_a_failed_record(log):
    """A failure before initiation must still project, not crash the fold."""
    rail = CreditRail(log)

    with pytest.raises(InsufficientCredits):
        rail.settle("mch_1", "m_a", "m_b", 1200, correlation_id="c1")

    settlements = list(fold(log.read_all()).settlements.values())
    assert len(settlements) == 1
    assert settlements[0].status == SettlementStatus.FAILED
    assert settlements[0].amount == 1200


def test_credit_settlement_events_carry_the_correlation_id(log):
    log.append("house", CREDITS_TRANSFERRED,
               {"from_actor_id": "house", "to_actor_id": "m_a", "amount": 5000},
               correlation_id="seed")
    rail = CreditRail(log)

    rail.settle("mch_1", "m_a", "m_b", 1200, correlation_id="c1")

    assert len(log.read_by_correlation("c1")) == 3  # initiated, transferred, completed


# --- where the rail gets the balance it checks ------------------------------
#
# The rail is the lock on the points ledger. Folding the whole log for one
# balance is linear in the log and the auction pays at least 25 contributors a
# lot, so `Exchange` binds a faster derivation of the same figure. What must
# NOT change is who supplies it: a checker handed the number it checks is not a
# checker.


def test_the_rail_folds_the_log_itself_when_nothing_is_bound(log):
    """The default has to stay a full fold: the rail is usable on its own."""
    log.append("house", CREDITS_TRANSFERRED,
               {"from_actor_id": "house", "to_actor_id": "m_a", "amount": 5000},
               correlation_id="seed")

    assert CreditRail(log)._balance_of("m_a") == 5000
    assert CreditRail(log)._balance_of("nobody") == 0


def test_settle_takes_no_balance_argument(log):
    """Structural, and deliberately so. A per-call balance is the defect this
    system keeps rediscovering: the constrained party naming its own figure.
    The lookup is bound once at wiring time and asked about the payer."""
    import inspect

    params = set(inspect.signature(CreditRail.settle).parameters)

    assert "balance" not in params
    assert not any("balance" in p for p in params)


def test_a_bound_lookup_is_asked_about_the_payer_and_nobody_else(log):
    asked = []

    def lookup(actor_id):
        asked.append(actor_id)
        return 5000

    rail = CreditRail(log, balance_of=lookup)
    rail.settle("mch_1", "m_a", "m_b", 1200, correlation_id="c1")

    assert asked == ["m_a"], "the rail asks about the actor it is charging"


def test_a_bound_lookup_still_refuses_a_short_balance_and_logs_first(log):
    """The whole failure path has to survive the optimisation: refused, and
    recorded before it raises."""
    rail = CreditRail(log, balance_of=lambda actor_id: 100)

    with pytest.raises(InsufficientCredits):
        rail.settle("mch_1", "m_a", "m_b", 1200, correlation_id="c1")

    events = log.read_by_correlation("c1")
    assert [e.type for e in events] == [SETTLEMENT_FAILED]
    assert "100" in events[0].payload["reason"]


def test_binding_a_balance_source_replaces_the_full_fold(log):
    log.append("house", CREDITS_TRANSFERRED,
               {"from_actor_id": "house", "to_actor_id": "m_a", "amount": 5000},
               correlation_id="seed")
    rail = CreditRail(log)

    rail.bind_balance_source(lambda actor_id: 0)

    with pytest.raises(InsufficientCredits):
        rail.settle("mch_1", "m_a", "m_b", 1200, correlation_id="c1")


# --- INR rail ---------------------------------------------------------------

def test_razorpay_settlement_completes_when_a_payment_is_captured(log):
    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_abc", "status": "captured"}]}
    })
    rail = RazorpayRail(log, fake)

    settlement = rail.settle("mch_1", "m_buyer", "m_seller", 970000, correlation_id="c1")

    assert settlement.status == SettlementStatus.COMPLETED
    assert settlement.razorpay_order_id == "order_1"
    assert settlement.razorpay_payment_id == "pay_abc"


def test_razorpay_settlement_sends_the_amount_in_paise(log):
    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_abc", "status": "captured"}]}
    })
    rail = RazorpayRail(log, fake)

    rail.settle("mch_1", "m_buyer", "m_seller", 970000, correlation_id="c1")

    assert fake.created[0]["amount"] == 970000
    assert fake.created[0]["currency"] == "INR"


def test_razorpay_settlement_stays_pending_when_no_payment_arrives(log):
    fake = FakeRazorpay(payments_by_order={})
    rail = RazorpayRail(log, fake, poll_attempts=2, poll_interval=0)

    settlement = rail.settle("mch_1", "m_buyer", "m_seller", 970000, correlation_id="c1")

    assert settlement.status == SettlementStatus.PENDING
    types = [e.type for e in log.read_by_correlation("c1")]
    assert SETTLEMENT_INITIATED in types
    assert SETTLEMENT_COMPLETED not in types


def test_razorpay_failure_to_create_an_order_is_logged_as_failed(log):
    rail = RazorpayRail(log, FakeRazorpay(fail_on_create=True))

    settlement = rail.settle("mch_1", "m_buyer", "m_seller", 970000, correlation_id="c1")

    assert settlement.status == SettlementStatus.FAILED
    assert SETTLEMENT_FAILED in [e.type for e in log.read_by_correlation("c1")]
    assert fold(log.read_all()).settlements[
        settlement.settlement_id
    ].status == SettlementStatus.FAILED


def test_an_uncaptured_payment_does_not_complete_the_settlement(log):
    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_abc", "status": "authorized"}]}
    })
    rail = RazorpayRail(log, fake, poll_attempts=1, poll_interval=0)

    settlement = rail.settle("mch_1", "m_buyer", "m_seller", 970000, correlation_id="c1")

    assert settlement.status == SettlementStatus.PENDING


def test_razorpay_settlement_records_a_payment_link_url(log):
    """Without a link there is no route to ever pay the order."""
    fake = FakeRazorpay(payment_link_url="https://rzp.io/rzp/ABC123")
    rail = RazorpayRail(log, fake, poll_attempts=1, poll_interval=0)

    rail.settle("mch_1", "m_buyer", "m_seller", 970000, correlation_id="c1")

    initiated = [
        e for e in log.read_by_correlation("c1") if e.type == SETTLEMENT_INITIATED
    ][0]
    assert initiated.payload["payment_link_url"] == "https://rzp.io/rzp/ABC123"
    assert initiated.payload["payment_link_error"] is None


def test_settlement_survives_a_payment_link_failure(log):
    """A missing link is recoverable; discarding a real Razorpay order is not."""
    fake = FakeRazorpay(fail_payment_link=True)
    rail = RazorpayRail(log, fake, poll_attempts=1, poll_interval=0)

    settlement = rail.settle("mch_1", "m_buyer", "m_seller", 970000, correlation_id="c1")

    assert settlement.status == SettlementStatus.PENDING
    assert settlement.razorpay_order_id == "order_1"
    initiated = [
        e for e in log.read_by_correlation("c1") if e.type == SETTLEMENT_INITIATED
    ][0]
    assert initiated.payload["payment_link_url"] is None
    assert initiated.payload["payment_link_error"] is not None
    assert "payment link service unavailable" in initiated.payload["payment_link_error"]
