"""The INR rail — Razorpay test mode.

Creates an order, then waits for a captured payment against it. A settlement
that never sees a capture stays PENDING rather than failing: pending is a real
state the accountant later reconciles, and pretending otherwise is exactly the
drift this system is built to catch.
"""
from __future__ import annotations

import time

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.ids import new_id
from exchange.models import Currency, Settlement, SettlementStatus


class RazorpayRail:
    def __init__(
        self,
        log: EventLog,
        client,
        poll_attempts: int = 3,
        poll_interval: float = 1.0,
    ) -> None:
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

        payment_link_url = None
        payment_link_error = None
        try:
            link = self._client.payment_link.create({
                "amount": amount,
                "currency": "INR",
                "description": f"Exchange settlement {settlement_id}",
                "notes": {"match_id": match_id, "settlement_id": settlement_id},
            })
            payment_link_url = link.get("short_url")
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
                "payment_link_url": payment_link_url,
                "payment_link_error": payment_link_error,
            },
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

        payment_id = self._await_capture(order["id"])
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

    def _await_capture(self, razorpay_order_id: str) -> str | None:
        for attempt in range(self._poll_attempts):
            payments = self._client.order.payments(razorpay_order_id)
            for item in payments.get("items", []):
                if item.get("status") == "captured":
                    return item["id"]
            if attempt < self._poll_attempts - 1:
                time.sleep(self._poll_interval)
        return None
