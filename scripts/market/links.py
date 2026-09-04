"""What is on the Razorpay account's payment links, and freeing a slot.

    .venv/bin/python -m scripts.market.links                  # look only
    .venv/bin/python -m scripts.market.links --cancel-unpaid  # free slots

WHY THIS EXISTS. Razorpay test mode allows thirty payment links per account,
ever. Once the account is at the cap, `payment_link.create` fails and every
new settlement comes back with a real order id and `pay_url: null` — the
money side is fine, the clickable link is simply not there. That is a
confusing thing to discover in front of an audience, so it is worth being
able to see the count and act on it.

WHAT --cancel-unpaid TOUCHES, AND WHAT IT NEVER TOUCHES. Only links whose
status is `created` — never `paid`. The paid ones are the campaign-to-cash
evidence: they carry `notes["campaign"]`, and `house/attribution.py` reads
them to answer what each campaign earned. Cancelling one would delete a
finding, not free a slot worth having.

CANCELLING IS NOT REVERSIBLE. So the default is to look, and the flag has to
be asked for by name.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Razorpay payment links.")
    parser.add_argument("--cancel-unpaid", action="store_true",
                        help="cancel every link still awaiting payment, to "
                             "free slots under the test-mode cap of 30")
    args = parser.parse_args(argv)

    import razorpay
    from dotenv import load_dotenv

    load_dotenv()
    key, secret = (os.environ.get("RAZORPAY_KEY_ID"),
                   os.environ.get("RAZORPAY_KEY_SECRET"))
    if not (key and secret):
        print("  RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are not both in .env.")
        return 1

    client = razorpay.Client(auth=(key, secret))
    items = client.payment_link.all({"count": 100}).get("payment_links", [])
    counts = Counter(i["status"] for i in items)
    print(f"  {len(items)} payment links on the account")
    for status, n in sorted(counts.items()):
        print(f"    {status:<12} {n}")

    paid = [i for i in items if i["status"] == "paid"]
    earned = sum(i.get("amount_paid") or 0 for i in paid)
    print(f"  ₹{earned / 100:,.0f} collected across {len(paid)} paid links "
          f"— this is what campaign-to-cash reads.")

    unpaid = [i for i in items if i["status"] == "created"]
    if not args.cancel_unpaid:
        if len(items) >= 30:
            print(f"\n  AT THE CAP. New settlements will return a real order "
                  f"and no pay_url.")
            print(f"  {len(unpaid)} link(s) are unpaid and can be cancelled to "
                  f"free slots:")
            print("    .venv/bin/python -m scripts.market.links --cancel-unpaid")
        return 0

    if not unpaid:
        print("\n  Nothing to cancel: every link on the account is paid, and "
              "a paid link is evidence, not clutter.")
        return 0

    print(f"\n  cancelling {len(unpaid)} unpaid link(s). Paid links are not "
          f"touched.")
    freed = 0
    for link in unpaid:
        campaign = (link.get("notes") or {}).get("campaign", "")
        try:
            client.payment_link.cancel(link["id"])
            freed += 1
            print(f"    cancelled {link['id']}  "
                  f"₹{(link.get('amount') or 0) / 100:,.0f}  {campaign}")
        except Exception as error:                       # noqa: BLE001
            print(f"    could not cancel {link['id']}: "
                  f"{type(error).__name__}: {str(error)[:120]}")

    # Whether cancelling actually frees a slot is Razorpay's business rule,
    # not ours to assume. So it is tested rather than claimed.
    try:
        probe = client.payment_link.create({
            "amount": 100, "currency": "INR", "description": "slot check"})
        client.payment_link.cancel(probe["id"])
        print(f"\n  A slot opened. New settlements will carry a pay_url again.")
    except Exception as error:                           # noqa: BLE001
        print(f"\n  Still capped: {str(error)[:160]}")
        print("  Cancelling does not return a slot on this account. The order "
              "id is real either way — demo the order, not the link.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
