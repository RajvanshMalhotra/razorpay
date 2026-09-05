# The demo, in order

Seven minutes. Two terminal commands, one browser. Nothing else to remember.

## Before you start

```bash
.venv/bin/python -m scripts.replay.generate runs/market.db docs      # rebuild pages
.venv/bin/python -m scripts.serve --merchant m_morningside --port 8795   # leave running
```

Wait for `35 items on the book`. Then open **http://localhost:8795/index.html**.

Everything below is one browser, switching merchants with the dropdown at the
top right. The pages are served by the exchange itself, which is why the last
step can spend real money.

---

## 1 · What it is  (1 min) — `index.html`

> Every business on Razorpay already pays and gets paid. Nobody has used that
> network to introduce them. We gave every merchant an agent that finds
> suppliers, argues the price down, and settles on real Razorpay payments.

Scroll to the network ring. **32 businesses, 36 trading relationships, no
introductions.**

## 2 · A business, and its four agents  (1 min) — pick **Bl Thirdwave**

Four cards, each in its own words: Trader, Scout, Diplomat, Subconscious.
Three act. The fourth only watches and remembers.

> Three settled, one walked away. It walked because the price never came down.

## 3 · One trade, end to end  (2 min) — tab **Where the money went**

The cold brew deal. Every station carries its event number.

| event | what happened |
|---|---|
| 205 | wanted 160 units of cold brew |
| 217 | shortlisted three sellers, argued for one |
| 256 | agreed at ₹195 a unit |
| **258** | **the gate refused it** — unknown supplier, over the first-deal cap |
| 259 | came back smaller |
| 261 | ₹4,875 on a real Razorpay order |

> **The refusal is recorded, not just the outcome.** The gate ruled before any
> money moved, and the record exists whether it said yes or no.

## 4 · It broke, and it fixed itself  (1 min) — same rail, further down

| event | what happened |
|---|---|
| 763 | Razorpay said captured. Our books said pending. Nobody was watching. |
| 936 | froze that one merchant — one business's disagreement must not stop everybody |
| 937 | repaired it using the payment id **Razorpay gave back**, not one we invented |
| 938 | let it trade again — a hold that never lifts is a ban |

> 21 mismatches caught. No human touched any of them.

## 5 · The part only Razorpay can sell  (1 min)

Still on **Bl Thirdwave**, scroll to **What your category clears at**:

> Cold Brew Concentrate clears at **₹195** against a **₹210** ask.
> 52% of trades close under the ask.

Now switch the dropdown to **Packmate**. Same card, no numbers:

> *"5 categories now have a clearing price of their own, and in 4 of them
> sellers are settling below their own ask. Which ones, and by how much, is on
> the Market plan."*

> A seller sees its own asks. A buyer sees its own bills. Only the processor
> settles both sides of every trade, so only Razorpay can say what the middle
> is. Thirdwave pays for that. Packmate does not.

The lock is real — the figures are not in Packmate's page at all.

## 6 · The books keep themselves  (30 sec)

Tab **Where the money went** → **Open your tab in Google Sheets**.

> One tab per business, written from the audit trail. Nobody typed any of it.

## 7 · LIVE — the empty merchant  (2 min) — dropdown to **Morningside**

₹0. Nothing on its book. Four agents that have never done anything.

Tab **Ask for something**. Type in plain words:

    compostable poly mailers          qty 40      cap ₹220

Press **Find it**, and read the screen as it fills:

    YOU          compostable poly mailers
    YOUR AGENT   threadbare will sell 40 at ₹9 each — ₹360 in total
                 chosen from 3 on the book         [Buy it] [No thanks]

> Nothing is committed yet. Say no and no money moves.

Press **Buy it**:

    THE GATE     allowed
    RAZORPAY     Paid ₹360 on order order_...
    THE LOG      5 events written, numbered 1024 to 1028

> A person typed a sentence. An agent searched a real order book, picked a
> counterparty, the gate ruled before the money, and Razorpay took the payment.
> Five events, one trade id, all of it lookupable.

**Reload the page** — the trade is now on Morningside's rail, and the figures at
the top are no longer zero.

---

## If something goes wrong

**Morningside is not empty any more** (you rehearsed on it) — 30 seconds:

```bash
.venv/bin/python -m scripts.market.join m_newname
.venv/bin/python -m scripts.replay.generate runs/market.db docs
.venv/bin/python -m scripts.serve --merchant m_newname --port 8795
```

**Rehearse on `m_daybreak`, never on the merchant you are demoing.** A quote
posts a real order, so asking spends the page.

**The ask box answers without the server** by word-matching the catalogue baked
into the page. If you see results but no `THE GATE` line, the server is not
running and you are looking at the fallback.

**`pay_url` is null** — the Razorpay test account is at its 30 payment-link cap.
The order id is real; the clickable link is not there. Say "on a real Razorpay
order" and show the order id.

## The one line to end on

> Payments told you a transaction happened. This tells you who should be doing
> business — and proves every rupee of it.
