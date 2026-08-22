"""Probe what the Razorpay test-mode account actually permits.

Run this before building anything on the INR rail. It answers one question:
how does a payment get created and captured without a browser checkout?
"""
from __future__ import annotations

import json
import sys

import razorpay

from exchange.config import Config


def main() -> int:
    cfg = Config.from_env()
    client = razorpay.Client(auth=(cfg.razorpay_key_id, cfg.razorpay_key_secret))

    print("=== 1. Create an order ===")
    order = client.order.create({
        "amount": 970000,          # paise; 500 units @ 19.40
        "currency": "INR",
        "receipt": "probe_receipt_1",
        "notes": {"probe": "exchange-core-task-1"},
    })
    print(json.dumps(order, indent=2))
    order_id = order["id"]

    print("\n=== 2. Fetch payments for that order ===")
    payments = client.order.payments(order_id)
    print(json.dumps(payments, indent=2))
    print("If count == 0, no payment exists yet and one cannot be willed "
          "into existence server-side.")

    print("\n=== 3. Try a payment link ===")
    try:
        link = client.payment_link.create({
            "amount": 970000,
            "currency": "INR",
            "description": "Exchange core probe",
            "notes": {"probe": "exchange-core-task-1"},
        })
        print(json.dumps(link, indent=2))
        print("\nOpen short_url in a browser, pay with test card "
              "4111 1111 1111 1111, any future expiry, any CVV.")
    except Exception as exc:  # noqa: BLE001 - probe script, report anything
        print(f"payment_link.create failed: {type(exc).__name__}: {exc}")

    print("\n=== 4. Order state after ===")
    print(json.dumps(client.order.fetch(order_id), indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
