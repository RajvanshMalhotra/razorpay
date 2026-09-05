# What to type in the ask box

**This is live.** Pressing Find it sends the merchant's own agent out on the
real order book. It searches, picks a supplier, negotiates if it has to, the
gate rules on the money, and Razorpay takes a real payment. About **twelve
seconds** end to end, and nothing is pacing it — a step appears when the event
behind it is written.

Say the quantity and the price in the sentence. Both are read and used.

---

## Use this one

```
cold brew concentrate 200 units under 300 each
```

**What it did when I ran it** — a real trade, so the seller and the price will
differ slightly each time:

| stage | what appeared |
|---|---|
| Posting what you need | 200 units · at most ₹300 each |
| Finding who can supply it | **packmate** · from 3 candidates |
| Checking it against your limits | **refused, then allowed** |
| Paying | **₹4,876** · on a real Razorpay order |

Then reload the page and the trade is on the merchant's rail with its event
numbers.

---

## Others that work

```
compostable poly mailers 300 units under 25 each
bubble wrap rolls 250 units under 40 each
packing tape rolls 400 units under 30 each
recycled kraft paper reels 500 units under 60 each
blank cotton tshirts 200 units under 200 each
```

There are **185 listings** on the book across 36 businesses, so most plain
sentences about real goods will find something.

---

## What to say while it runs

> I am not filling in a form. No supplier directory, no RFQ, no three quotes by
> email. I type what I need the way I would say it.

*(press Find it, then switch to the Razorpay desk tab)*

> And here it is on Razorpay's side, right now. The merchant sees plain
> English. Razorpay sees the event numbers.

*(switch back as the steps land)*

> It searched the real book, looked at three sellers, and picked one — and it
> says why. **Then the gate ruled before any money moved.** Paid on a real
> Razorpay order.

*(reload the page)*

> And the zeroes are gone. Sixty seconds ago this business had nothing.

---

## Two things to know

**Every ask is a real purchase.** It spends the merchant. That is the demo
working — an empty business really does end up with a supplier and a payment.
But it means **"starts empty" is one-shot**, so rehearse on a merchant you are
not recording.

**To reset to a fresh empty business** — about thirty seconds:

```bash
.venv/bin/python -m scripts.market.fresh
```

It registers a business that has never traded, gives it a shelf, rebuilds the
pages, and prints the serve command with the name it picked. Nothing is
deleted — the spent merchants keep their trades, because those trades
happened.

**If nothing matches** what you typed, the agent says so rather than inventing
a supplier — which is the honest answer, but not the one you want on camera.
Stick to goods that are actually on the book; the list above is safe.
