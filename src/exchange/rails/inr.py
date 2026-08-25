"""The INR rail — Razorpay test mode.

Creates an order, then waits for a captured payment against it. A settlement
that never sees a capture stays PENDING rather than failing: pending is a real
state the accountant later reconciles, and pretending otherwise is exactly the
drift this system is built to catch.

EVERY RAZORPAY CALL HERE IS A RECORDED OUTCOME, never an exception out of
`settle()`. `order.create` failing is a failed settlement; `payment_link.create`
failing leaves the order standing with the reason in the payload; and the
capture poll — the one call that runs AFTER SETTLEMENT_INITIATED — degrades to
PENDING with a CAPTURE_POLL_FAILED beside it. A long run dies on the first 429
otherwise, and it dies holding an ALLOW that never resolves.
"""
from __future__ import annotations

import time

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.ids import new_id
from exchange.models import Currency, Settlement, SettlementStatus
from exchange.rails.capture import find_captured_payment


class RazorpayRail:
    def __init__(
        self,
        log: EventLog,
        client,
        poll_attempts: int = 1,
        poll_interval: float = 0.0,
    ) -> None:
        """ONE POLL, NO SLEEP, by default.

        The old defaults were three attempts a second apart. That is three
        Razorpay round-trips and two seconds of `time.sleep` inside every
        settlement — 900 calls and roughly ten minutes of pure sleeping across
        300 settlements — spent establishing something this codebase already
        knows: a payment cannot be created server-side in test mode
        (`docs/razorpay-test-mode-findings.md`), so no amount of polling inside
        `settle()` can make a capture appear that nobody has made yet.

        Polling harder is the wrong mechanism for a capture that lands
        asynchronously anyway. `Accountant.reconcile` is the right one: it sees
        the capture whenever it arrives, records it as a drift, and `repair`
        completes the settlement from the remote's own answer. Sleeping in
        `settle()` meanwhile blocks the whole market, because the market is one
        process.

        One attempt rather than zero because a payment link paid before
        `settle()` returns is real, cheap to notice, and free of any sleep. A
        runner that genuinely drives payment links and wants to wait passes its
        own figures; this default is for the automated case, which is all of
        them today.
        """
        self._log = log
        self._client = client
        self._poll_attempts = poll_attempts
        self._poll_interval = poll_interval

    def settle(
        self,
        match_id: str,
        from_actor_id: str,
        to_actor_id: str,
        amount: int,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> Settlement:
        settlement_id = new_id("stl")

        try:
            order = self._client.order.create({
                "amount": amount,
                "currency": "INR",
                "receipt": settlement_id,
                "notes": {
                    "match_id": match_id,
                    "buyer": from_actor_id,
                    "seller": to_actor_id,
                },
            })
        except Exception as exc:  # noqa: BLE001 - any SDK failure is a failed settlement
            self._log.append(
                from_actor_id,
                ev.SETTLEMENT_FAILED,
                {
                    "settlement_id": settlement_id,
                    "match_id": match_id,
                    "currency": str(Currency.INR),
                    "amount": amount,
                    "reason": f"{type(exc).__name__}: {exc}",
                },
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            return Settlement(
                settlement_id=settlement_id,
                match_id=match_id,
                currency=Currency.INR,
                amount=amount,
                status=SettlementStatus.FAILED,
            )

        # THE PAYABLE ORDER IS THE LINK'S, NOT THE ONE ABOVE.
        #
        # `payment_link.create` mints an order of its own, and a payment made
        # against the link lands there. Proved against live test mode: paying a
        # link left our own order on `{"count": 0, "items": []}` forever while
        # the capture sat under the link's order.
        #
        #     our order    order_TTxxla9S5HPqMw -> 0 payments, permanently
        #     the capture  pay_TU3FFa0irsSlhq   -> order_TU36coXRQ7wsMY
        #
        # `_await_capture` polls `order.payments(...)`, so pointed at our order
        # it could never see a payment, and EVERY INR settlement would stay
        # PENDING no matter who paid. Nothing downstream would engage: no
        # ORDER_FILLED, no POINTS_MINTED, confidence pinned at zero, and the
        # house agent mining an empty set.
        #
        # No fake client could have caught this — a stub returns whatever the
        # test tells it to. It took paying a real link to find, which is why
        # the ids above stay in this comment.
        #
        # The order above is kept as the settlement's own receipt (it carries
        # `receipt=settlement_id` and the counterparties in `notes`, which the
        # link's order does not). `razorpay_order_id` records the PAYABLE one,
        # because that is the id every reader — this poll and the accountant's
        # reconcile alike — must ask about.
        payment_link_url = None
        payment_link_error = None
        payment_link_id = None
        try:
            link = self._client.payment_link.create({
                "amount": amount,
                "currency": "INR",
                "description": f"Exchange settlement {settlement_id}",
                "notes": {"match_id": match_id, "settlement_id": settlement_id},
                "reference_id": settlement_id,
            })
            payment_link_url = link.get("short_url")
            payment_link_id = link.get("id")
        except Exception as exc:  # noqa: BLE001 - the order still stands without a link
            payment_link_error = f"{type(exc).__name__}: {exc}"

        initiated = self._log.append(
            from_actor_id,
            ev.SETTLEMENT_INITIATED,
            {
                "settlement_id": settlement_id,
                "match_id": match_id,
                "currency": str(Currency.INR),
                "amount": amount,
                "razorpay_order_id": order["id"],
                "payment_link_id": payment_link_id,
                "payment_link_url": payment_link_url,
                "payment_link_error": payment_link_error,
            },
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

        payment_id = self._await_capture(
            order["id"],
            payment_link_id=payment_link_id,
            settlement_id=settlement_id,
            actor_id=from_actor_id,
            correlation_id=correlation_id,
            causation_id=initiated.event_id,
        )
        if payment_id is None:
            return Settlement(
                settlement_id=settlement_id,
                match_id=match_id,
                currency=Currency.INR,
                amount=amount,
                status=SettlementStatus.PENDING,
                razorpay_order_id=order["id"],
            )

        self._log.append(
            from_actor_id,
            ev.SETTLEMENT_COMPLETED,
            {"settlement_id": settlement_id, "razorpay_payment_id": payment_id},
            correlation_id=correlation_id,
            causation_id=initiated.event_id,
        )

        return Settlement(
            settlement_id=settlement_id,
            match_id=match_id,
            currency=Currency.INR,
            amount=amount,
            status=SettlementStatus.COMPLETED,
            razorpay_order_id=order["id"],
            razorpay_payment_id=payment_id,
        )

    def _await_capture(
        self,
        razorpay_order_id: str,
        payment_link_id: str | None,
        settlement_id: str,
        actor_id: str,
        correlation_id: str,
        causation_id: str | None,
    ) -> str | None:
        """Look for a captured payment. Never raise; a failed look is an event.

        THE ONLY CALL IN `settle()` THAT RUNS AFTER THE EXPOSURE IS COMMITTED,
        which is what made it the dangerous one. SETTLEMENT_INITIATED is
        already in the log; a 429 or a dropped connection here used to
        propagate out of `execute_match` and kill the process, leaving an ALLOW
        with no settlement outcome — the hole BUG-7 was filed to close,
        reopened on the other rail — and losing every hour of the run behind
        it.

        A FAILED POLL IS NOT A FAILED SETTLEMENT, and must not be recorded as
        one. The Razorpay order exists and is payable; all that failed is our
        attempt to look. So the honest outcome is to stop polling and return
        PENDING, which is a real state the accountant already reconciles
        correctly — and to say in the log that we stopped and why, because a
        settlement that stayed PENDING because nobody paid and one that stayed
        PENDING because we could not ask are different facts, and the run's
        post-mortem needs to tell them apart. Silence here would be the
        swallowed error this project refuses everywhere else.
        """
        for attempt in range(self._poll_attempts):
            try:
                found = find_captured_payment(
                    self._client,
                    payment_link_id=payment_link_id,
                    razorpay_order_id=razorpay_order_id,
                )
            except Exception as exc:  # noqa: BLE001 - a failed look, not a failed payment
                self._log.append(
                    actor_id,
                    ev.CAPTURE_POLL_FAILED,
                    {
                        "settlement_id": settlement_id,
                        "razorpay_order_id": razorpay_order_id,
                        "attempt": attempt + 1,
                        "reason": f"{type(exc).__name__}: {exc}",
                    },
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                )
                return None
            if found:
                return found
            if attempt < self._poll_attempts - 1:
                time.sleep(self._poll_interval)
        return None
