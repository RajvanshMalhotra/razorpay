# Screen-recording script

Roughly six minutes. Left column is what you do. Right column is what you say.
Every number here is read from the log — none of it is written by hand.

**Before you hit record**

```bash
.venv/bin/python -m scripts.replay.generate runs/market.db docs
.venv/bin/python -m scripts.serve --merchant m_sunrise --port 8795
```

Open `http://localhost:8795/index.html`. Close every other tab. You switch
businesses with the dropdown at the top right — that is the whole navigation.

---

## 1 · The problem  (0:00 – 0:35)

**On screen:** the front page, still. Let the network ring finish drawing.

> Every business on Razorpay already pays other businesses and gets paid by
> them. All day. That means Razorpay can already see who buys what, from whom,
> and at what price.
>
> Nobody has ever used that to introduce them to each other.
>
> So a coffee shop in Bangalore pays too much for cold brew, while a supplier
> two kilometres away is sitting on stock. They will never meet. There is no
> reason for that except that nobody built the thing that connects them.
>
> That is what this is. Every merchant gets an agent. The agents find each
> other, argue about price, and settle on real Razorpay payments.

---

## 2 · What it did  (0:35 – 1:10)

**On screen:** scroll slowly across the four figures at the top.

> This is a real run, not a mock-up. Thirty-five businesses. **₹145,093**
> actually settled between their agents. **Forty** trading partnerships that
> nobody introduced. **Seventy-seven** decisions recorded.
>
> Nobody typed any of these numbers. They are counted from an audit trail that
> only ever gets added to.

**On screen:** hover two or three nodes on the network ring.

> Each dot is a business. Each line is a real trade. None of these
> relationships existed before the agents found each other.

---

## 3 · A merchant's own screen  (1:10 – 2:00)

**On screen:** dropdown → **Bl Thirdwave**. Land on "My agents".

> This is what a merchant sees. One page.
>
> Your agent is four parts, and each remembers different things. The **Trader**
> buys and sells. The **Scout** watches what is rising in the market. The
> **Diplomat** decides who is worth dealing with. The **Subconscious** never
> acts — it only watches and remembers, so the next deal starts smarter than
> the last one.
>
> And each card is in the agent's own words. This is not a summary I wrote.
> It is what that part of the agent actually said, quoted from the log.

**On screen:** point at "3 deals done, 1 walked away".

> Three deals done. One walked away — because the price never came down.
> An agent that never walks away is not negotiating, it is just agreeing.

---

## 4 · The money it saves  (2:00 – 2:50)  ← *the revenue point*

**On screen:** tab → **Where the money went**. Open the cold brew trade.

> Here is one trade, from beginning to end. Every step carries the number of
> the event it came from, so you can look any of it up.
>
> The merchant needed a hundred and sixty units of cold brew. The seller was
> asking **₹210** a unit. The agent argued it down to **₹195**.

**On screen:** let the offers scroll — the real back-and-forth.

> These are the actual sentences the two agents said to each other. Not a
> simulation of a negotiation. The negotiation.
>
> Fifteen rupees a unit does not sound like much. Across everything these
> agents bought — **₹145,000** of buying — they haggled **₹11,169** off the
> asking price. That is about **eight percent**, and it is pure margin. It
> stays in the merchant's pocket.
>
> No human sat in any of those conversations.

---

## 5 · It is bounded, and it proves it  (2:50 – 3:30)

**On screen:** stay on the same rail, rest on the gate station.

> Then look at this step. The gate **refused it**.
>
> The supplier was unknown, and the amount was over the cap for a first deal
> with someone you have never traded with. So the agent came back smaller, and
> that was allowed.
>
> Here is the part that matters: **the refusal is written down.** Not just the
> outcome — the ruling itself, before any money moved, whether it said yes or
> no. Seventy-eight rulings in this run. Twenty-five of them said no.
>
> An agent spending your money without that is not a product you can sell to a
> business.

---

## 6 · It breaks safely  (3:30 – 4:05)

**On screen:** scroll down the same rail to the four red stations.

> And then it broke.
>
> Razorpay said a payment was captured. Our books said pending. Nobody was
> watching — the accountant found it on a routine sweep.
>
> It froze **that one merchant**, because one business's disagreement must not
> stop everybody else. It repaired the settlement using the payment id
> **Razorpay gave back** — not one we invented, because a repair that invents
> an id is a machine lying about money. Then it let the merchant trade again,
> because a hold that never lifts is a ban.
>
> Twenty-one mismatches caught this way. No human touched any of them.

---

## 7 · The thing only Razorpay can sell  (4:05 – 4:50)  ← *the business model*

**On screen:** scroll to **What your category clears at** on Thirdwave's page.

> Now the part a merchant will pay for.
>
> Cold brew **clears at ₹195**, against a **₹210** asking price. Half of all
> buyers get a discount. So if you are paying the ask, you are leaving money
> on the table, and you should push.
>
> Electronics assembly closes at the asking price every single time. So do not
> push there — you will spend the one thing an agent cannot buy more of, which
> is the other side's patience.
>
> A seller only sees its own prices. A buyer only sees its own bills. **Only
> the company that settles both sides of every trade can say what the middle
> is.** That is Razorpay, and nobody else.

**On screen:** switch the dropdown to **Packmate**. Same card, no numbers.

> This merchant is on the standard plan. It is told a gap exists. It is not
> told where.
>
> That is the upgrade. And the lock is real — those numbers are not hidden on
> this page, they were never put in it.

---

## 8 · The books keep themselves  (4:50 – 5:15)

**On screen:** back to Thirdwave → books card → **Open your tab in Google Sheets**.

> Every business gets its own tab, written straight from the audit trail.
> What it bought, what the seller was asking, what it actually paid, and what
> the agent saved it.
>
> A merchant does not do bookkeeping for this. It reads it.

---

## 9 · Live — the whole thing in one minute  (5:15 – 6:15)

**On screen:** dropdown → **Sunrise**. Show the zeroes.

> Last thing, and this one is live.
>
> This business joined today. Nothing on its books. Four agents that have never
> done anything.

**On screen:** tab **Ask for something**. Type slowly: `compostable poly mailers`

> You do not fill in a form. You type what you need, the way you would say it.

**On screen:** press **Find it**. Read the screen as it fills.

> It searched the real order book — the same search its agents use — looked at
> three sellers, and picked one. Forty units at nine rupees each.
>
> Nothing has been committed yet. If I say no thanks, no money moves.

**On screen:** press **Buy it**.

> The gate ruled first. Then Razorpay took the payment, on a real order.
> And five events were written, numbered, so anyone can look up exactly what
> happened and in what order.

**On screen:** reload the page. The zeroes are gone.

> That is a business that had nothing sixty seconds ago, with a supplier, a
> payment, and books.

---

## 10 · Close  (6:15 – 6:30)

**On screen:** back to the front page. Let it sit.

> So: it finds businesses their next supplier. It argues the price down and
> keeps the difference in their pocket. It keeps their books. It refuses
> anything outside the limits they set, and writes down why. It repairs itself
> when the money and the books disagree.
>
> And it sells the one thing only the payments company can see — what a market
> actually clears at.
>
> **Payments told you a transaction happened. This tells you who you should be
> doing business with — and proves every rupee of it.**

---

## The numbers, if anyone asks

| | |
|---|---|
| Settled between agents | ₹145,093 |
| Saved by negotiating | ₹11,169 — about 8% of what was spent |
| Businesses | 35 |
| Partnerships nobody introduced | 40 |
| Gate rulings recorded | 78, of which 25 refused |
| Book mismatches caught and repaired | 21 |
| Events in the log | 1,028 |
| Humans in the loop | 0 |

## Two honest notes

The Razorpay test account is at its thirty payment-link cap, so a new trade
returns a real order id and no clickable link. Say **"on a real Razorpay
order"** and show the order id — that is what the money moved on.

Rehearse on **m_daybreak**, never on the merchant you are recording. Asking a
question posts a real order, so a rehearsal spends the empty page.
