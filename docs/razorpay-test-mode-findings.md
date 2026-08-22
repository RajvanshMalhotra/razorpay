# Razorpay test-mode findings

> **STATUS: PROBE NOT YET EXECUTED.**
>
> This document is a placeholder created during Task 1 scaffolding. No call
> was made against the Razorpay API to produce the answers below — this
> session had no `.env` and no real Razorpay test-mode credentials available,
> and was explicitly instructed not to fabricate probe output.
>
> **Before Task 8 (the INR rail / `RazorpayRail.settle()`) is trusted or
> built upon, a human with real Razorpay test-mode credentials must:**
> 1. Fill in `.env` from `.env.example` with real `rzp_test_...` keys.
> 2. Run `.venv/bin/python scripts/razorpay_probe.py`.
> 3. Open the printed `short_url` and pay with test card `4111 1111 1111 1111`
>    (any future expiry, any CVV), then re-run the relevant probe steps.
> 4. Replace every `NOT YET RUN — requires real test-mode credentials in .env`
>    field below with the real, observed answer — verbatim, no guessing.

Probed on: NOT YET RUN — requires real test-mode credentials in .env
Key id prefix: NOT YET RUN — requires real test-mode credentials in .env

## Can an order be created server-side?
NOT YET RUN — requires real test-mode credentials in .env

## Can a payment be created without a browser?
NOT YET RUN — requires real test-mode credentials in .env

## Does payment_link.create work on this account?
NOT YET RUN — requires real test-mode credentials in .env

## What is the working path to a captured payment?
NOT YET RUN — requires real test-mode credentials in .env

## Implication for the INR rail
NOT YET RUN — requires real test-mode credentials in .env

## Sample IDs captured
order_id: NOT YET RUN — requires real test-mode credentials in .env
payment_id: NOT YET RUN — requires real test-mode credentials in .env
