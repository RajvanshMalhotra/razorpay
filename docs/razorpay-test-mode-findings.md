# Razorpay test-mode findings

Probed on: 2026-08-23
Key id prefix: rzp_test_TSvC5q8Pkb...

## Can an order be created server-side?
Yes. `client.order.create(...)` returned a full order object immediately:

```json
{
  "amount": 970000,
  "amount_due": 970000,
  "amount_paid": 0,
  "attempts": 0,
  "created_at": 1787426872,
  "currency": "INR",
  "entity": "order",
  "id": "order_TSvlfCMLyvzRoF",
  "notes": {"probe": "exchange-core-task-1"},
  "offer_id": null,
  "receipt": "probe_receipt_1",
  "status": "created"
}
```

## Can a payment be created without a browser?
No. `client.order.payments(order_id)` on the freshly created order returned:

```json
{"entity": "collection", "count": 0, "items": []}
```

`count == 0` — no payment exists on the order, and none was created by any server-side call. A payment cannot be willed into existence server-side.

## Does payment_link.create work on this account?
Yes. `client.payment_link.create(...)` succeeded and returned a link object including a working `short_url`:

```json
{
  "accept_partial": false,
  "allow_full_payment": false,
  "amount": 970000,
  "amount_paid": 0,
  "cancelled_at": 0,
  "created_at": 1787426873,
  "currency": "INR",
  "customer": [],
  "description": "Exchange core probe",
  "expire_by": 0,
  "expired_at": 0,
  "first_min_partial_amount": 0,
  "id": "plink_TSvlgNs8fC4u15",
  "notes": {"probe": "exchange-core-task-1"},
  "notify": {"email": false, "sms": false, "whatsapp": false},
  "payment_plan": false,
  "payments": null,
  "reference_id": "",
  "reminder_enable": false,
  "reminders": [],
  "short_url": "https://rzp.io/rzp/HUlZkxfJ",
  "status": "created",
  "updated_at": 1787426873,
  "upi_link": false,
  "user_id": "",
  "whatsapp_link": false
}
```

The `short_url` was not opened/paid during this probe run, so the link's own status is still `created` and `payments` is `null` — the probe only confirms the link can be created, not the full pay-and-capture path.

## What is the working path to a captured payment?
The probe output does not show a captured payment — the link was never opened or paid during this run, so no payment record exists anywhere in the output. What the output does establish: `order.create()` works server-side, `payments_link.create()` works server-side and yields a payable `short_url`, but nothing server-side produces a payment. Re-fetching the order after link creation shows it unchanged (see below), confirming that link creation alone does not move a payment forward. The only path implied by the data collected is: create an order, create a payment link for that order's amount, then a human or browser automation opens `short_url` and pays with a test card — that final leg was not exercised in this run, so it is not verified here, only inferred from what did and did not happen.

## Implication for the INR rail
`RazorpayRail.settle()` (Task 8) creates an order and polls `order.payments()` for a captured payment. Because no payment can be created server-side, that poll will time out and the settlement will remain `PENDING` in any fully automated run. `PENDING` is a legitimate state that the accountant reconciles later, not a bug. Therefore Task 8's `settle()` will additionally create a Payment Link and record its `short_url` in the `SETTLEMENT_INITIATED` event payload, so a human or browser automation can complete payment with the test card `4111 1111 1111 1111` and produce a genuine captured payment id. The polling loop itself stays unchanged.

## Sample IDs captured
order_id: order_TSvlfCMLyvzRoF
plink_id: plink_TSvlgNs8fC4u15
payment_id: none — no payment was created or captured during this probe run. `order.payments()` returned `count: 0`, and the payment link's own `payments` field was `null` after creation. There is no payment id to record.

## Order state after payment-link creation
Re-fetching the order (`client.order.fetch(order_id)`) after the link was created showed it unchanged from step 1: `status: "created"`, `amount_paid: 0`, `amount_due: 970000`. Creating a payment link does not by itself advance the order's payment state.
