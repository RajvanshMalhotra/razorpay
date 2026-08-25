"""Where a captured payment actually is.

One function, because the answer is non-obvious and TWO callers need it — the
INR rail while settling, and the accountant while reconciling. They asked
different objects and both asked wrongly; the point of putting it here is that
there is now one place to be right.

WHAT LIVE TEST MODE ACTUALLY DOES, probed rather than assumed:

    payment_link.create(...)          -> id, short_url.  NO order_id.
                                                         NO payments.
    ...someone pays the link...
    payment_link.fetch(link_id)       -> order_id: order_TU36coXRQ7wsMY
                                         payments: [{payment_id, status:
                                                     "captured", ...}]

The order a payment lands on is minted BY THE LINK, WHEN SOMEONE PAYS. It does
not exist at settlement time, so no order id recorded during `settle()` can
ever be the one that receives the money. Polling `order.payments(our_order)`
returns `{"count": 0, "items": []}` permanently, however many payments are
made — which is exactly what happened: a real link was paid, the money was
captured, and our own order stayed empty forever.

So capture is discovered through the LINK. The order is kept only as a
fallback for a settlement whose link could not be created, where there is
nothing else to ask about.

This cost two wrong fixes to find. The first pointed the poll at the link's
order id — which is null at create time. The second was worse: the fake
returned an `order_id` the real API does not, so the suite went green on a fix
that could not work. A stub easier than the real thing tests the stub.
"""
from __future__ import annotations


def find_captured_payment(
    client,
    *,
    payment_link_id: str | None,
    razorpay_order_id: str | None,
) -> str | None:
    """The id of a captured payment for this settlement, or None.

    Raises whatever the SDK raises. Callers decide what a failed look means —
    for both of them it is a recorded event, never a failed settlement.
    """
    if payment_link_id:
        link = client.payment_link.fetch(payment_link_id)
        for payment in link.get("payments") or ():
            if payment.get("status") == "captured":
                return payment.get("payment_id") or payment.get("id")

        # A paid link names its order. Ask it directly: the link's `payments`
        # block is a summary, and the order is the authority on the payment.
        order_id = link.get("order_id")
        if order_id:
            found = _captured_on_order(client, order_id)
            if found:
                return found

    if razorpay_order_id:
        return _captured_on_order(client, razorpay_order_id)
    return None


def _captured_on_order(client, order_id: str) -> str | None:
    for item in client.order.payments(order_id).get("items", ()):
        if item.get("status") == "captured":
            return item["id"]
    return None
