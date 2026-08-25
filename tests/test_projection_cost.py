"""What a trade costs, and that it does not grow with the log.

The exchange is meant to run a 30-merchant market for hours against ONE
persistent log, and then to be re-run for tuning. So per-trade cost that grows
with log size is not a slow test, it is a design defect: linear per trade is
quadratic per run, and the second hour costs several times the first.

These tests pin the shape rather than the clock. A stopwatch on a loaded
machine is a coin toss; counting how much of the log a trade reads is exact,
and it is the thing that was actually wrong — four call sites re-read the whole
log on every trade. The measurement is ROWS READ, not calls made: a call to
`read_since(0)` returns the whole log while looking like an incremental read,
and only counting rows can tell the difference.

Every read of the log is counted, so these fail if any call site starts
re-scanning again — including one added later by someone who has not read this
file. That is their point.

No network: the Razorpay client is a fake, as everywhere else in this suite.
"""
from __future__ import annotations

import pytest

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.auction import pay_royalties
from exchange.models import (
    ActorStatus,
    Currency,
    Match,
    Order,
    SettlementStatus,
    Side,
)
from exchange.policy import PolicyContext
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.service import Exchange

TRUSTED = PolicyContext(
    actor_status=ActorStatus.ACTIVE, rolling_spend=0, counterparty_confidence=0.9
)

# Small enough to keep the suite fast, far enough apart that a per-trade full
# scan of the log cannot possibly produce the same count for both.
SMALL = 60
LARGE = 900


class CountingLog(EventLog):
    """An EventLog that records how much of itself each read touched."""

    def __init__(self, db_path: str) -> None:
        super().__init__(db_path)
        self.rows_read = 0
        self.full_reads = 0

    def read_all(self):
        events = super().read_all()
        self.full_reads += 1
        self.rows_read += len(events)
        return events

    def read_since(self, seq: int):
        events = super().read_since(seq)
        self.rows_read += len(events)
        return events

    def read_by_correlation(self, correlation_id: str):
        events = super().read_by_correlation(correlation_id)
        self.rows_read += len(events)
        return events

    def reset(self) -> None:
        self.rows_read = 0
        self.full_reads = 0


class AlwaysCaptured:
    """Every order this fake creates already has a captured payment."""

    def __init__(self) -> None:
        self.order = self._Orders()
        self.payment_link = self._Links()

    class _Orders:
        def __init__(self) -> None:
            self._n = 0

        def create(self, data):
            self._n += 1
            return {"id": f"order_{self._n}", "amount": data["amount"],
                    "status": "created"}

        def payments(self, order_id):
            return {"count": 1,
                    "items": [{"id": f"pay_{order_id}", "status": "captured"}]}

    class _Links:
        def create(self, data):
            return {"id": "plink_fake", "short_url": "https://rzp.io/rzp/FAKE"}


def _pad(log: CountingLog, n: int) -> None:
    """n events of the shapes a real run leaves behind."""
    for i in range(1, n + 1):
        log.append(f"m_{i % 30}", ev.ORDER_POSTED, {
            "order_id": f"ord_pad_{i}", "actor_id": f"m_{i % 30}",
            "side": str(Side.ASK), "asset_ref": f"ast_{i}", "asset_query": None,
            "qty": 10, "limit_price": 100 + i, "currency": str(Currency.INR),
            "expires_at": "2030-01-01T00:00:00+00:00", "policy_snapshot": {},
        }, correlation_id=f"pad_{i}")


def _exchange(tmp_path, name: str, padding: int) -> tuple[Exchange, CountingLog]:
    log = CountingLog(str(tmp_path / f"{name}.db"))
    log.append("genesis", ev.CREDITS_TRANSFERRED,
               {"from_actor_id": "genesis", "to_actor_id": "m_buyer",
                "amount": 100_000_000},
               correlation_id="seed")
    log.append("genesis", ev.CREDITS_TRANSFERRED,
               {"from_actor_id": "genesis", "to_actor_id": "house",
                "amount": 100_000_000},
               correlation_id="seed")
    _pad(log, padding)
    fake = AlwaysCaptured()
    ex = Exchange(log, index=None,
                  inr_rail=RazorpayRail(log, fake, poll_attempts=1),
                  credit_rail=CreditRail(log))
    return ex, log


def _ask(ex: Exchange, order_id: str, price: int = 2000) -> None:
    ex.post_order(Order(
        order_id=order_id, actor_id="m_seller", side=Side.ASK,
        asset_ref="ast_x", asset_query=None, qty=500, limit_price=price,
        currency=Currency.INR, expires_at="2030-01-01T00:00:00+00:00",
    ), correlation_id="trade")


def _trade(ex: Exchange, match_id: str, order_id: str):
    return ex.execute_match(
        Match(match_id=match_id, bid_order_id="ord_bid", ask_order_id=order_id,
              clearing_price=194, qty=500, score=0.9, rationale="cost test"),
        buyer_id="m_buyer", seller_id="m_seller", ctx=TRUSTED,
        correlation_id="trade", currency=Currency.INR,
    )


def _rows_for_one_trade(tmp_path, name: str, padding: int) -> int:
    """Rows of log read by ONE settled INR trade, once the run is under way.

    The first trade against a fresh `Exchange` warms the projection by folding
    the log once — a real cost, but a one-off per process, not a per-trade one.
    What a run actually spends is measured here: the second trade onward.
    """
    ex, log = _exchange(tmp_path, name, padding)
    _ask(ex, "ord_warm")
    _trade(ex, "mch_warm", "ord_warm")  # warms the projection

    _ask(ex, "ord_measured")
    log.reset()
    decision, settlement = _trade(ex, "mch_measured", "ord_measured")

    assert settlement is not None
    assert settlement.status == SettlementStatus.COMPLETED, "must settle for real"
    assert log.full_reads == 0, (
        f"{log.full_reads} full reads of the log inside one trade; "
        "the gate, the rails and the minter all read forward from the "
        "projection they already hold"
    )
    rows = log.rows_read
    log.close()
    return rows


def test_one_trade_reads_the_same_amount_of_log_whatever_the_log_holds(tmp_path):
    """The headline. A trade against a 900-event log must cost what a trade
    against a 60-event log costs — otherwise hour two of a run is slower than
    hour one, and the tuning re-runs are slower again."""
    small = _rows_for_one_trade(tmp_path, "small", SMALL)
    large = _rows_for_one_trade(tmp_path, "large", LARGE)

    assert small == large, (
        f"a trade read {small} rows against a {SMALL}-event log and {large} "
        f"against a {LARGE}-event one; per-trade cost is growing with the log"
    )


def test_one_trade_reads_only_the_events_it_appended(tmp_path):
    """Stronger than 'does not grow': the cost is bounded by the events NEW
    since the last read, which for one trade is a handful."""
    rows = _rows_for_one_trade(tmp_path, "bound", LARGE)

    assert rows <= 20, f"one trade read {rows} rows; it appends fewer than 10"


def test_royalty_payouts_do_not_refold_the_log_per_contributor(tmp_path):
    """The privacy floor guarantees at least 25 contributors, and the credits
    rail used to fold the whole log for each one's balance."""
    counts = {}
    for name, padding in (("roy_small", SMALL), ("roy_large", LARGE)):
        ex, log = _exchange(tmp_path, name, padding)
        ex.state()  # the one-off warm fold, kept out of the measurement
        log.reset()

        per_contributor, paid = pay_royalties(
            ex, asset_id="lot_1",
            contributor_ids=[f"contrib_{i}" for i in range(25)],
            clearing_price=100_000, correlation_id="roy",
        )

        assert paid == 25, "the payouts must actually happen to be measured"
        assert per_contributor > 0
        assert log.full_reads == 0
        counts[name] = log.rows_read
        log.close()

    assert counts["roy_small"] == counts["roy_large"], (
        f"25 payouts read {counts['roy_small']} rows against a {SMALL}-event "
        f"log and {counts['roy_large']} against a {LARGE}-event one"
    )


def test_the_projection_is_folded_once_and_then_extended(tmp_path):
    """The warm-up is a one-off. A second call reads only what arrived since."""
    ex, log = _exchange(tmp_path, "warmup", LARGE)

    log.reset()
    ex.state()
    cold = log.rows_read

    log.reset()
    ex.state()
    warm = log.rows_read

    assert cold >= LARGE, "the first fold reads the whole log, as it must"
    assert warm == 0, f"a second call re-read {warm} rows with nothing appended"
    log.close()


def test_a_second_exchange_over_the_same_log_sees_events_it_did_not_write(tmp_path):
    """The cache extends from the LOG, not from what this instance appended.

    This is what keeps the incremental projection honest across two readers of
    one database — and what keeps `_already_decided` a correctness guard rather
    than a per-instance memory of its own writes.
    """
    ex_a, log = _exchange(tmp_path, "shared", SMALL)
    ex_b = Exchange(log, index=None, inr_rail=None, credit_rail=CreditRail(log))
    ex_b.state()  # warm b's projection BEFORE a writes anything

    _ask(ex_a, "ord_shared")
    _trade(ex_a, "mch_shared", "ord_shared")

    assert "mch_shared" in ex_b.state().decided_action_refs
    with pytest.raises(ValueError, match="fresh match_id"):
        _trade(ex_b, "mch_shared", "ord_shared")
    log.close()
