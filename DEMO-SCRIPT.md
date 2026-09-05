# The demo, and what to say

Roughly seven minutes. Two commands, one browser window, two tabs.

Every figure below is read from the audit trail. Nothing is typed by hand, and
nothing here is a mock-up.

---

## Set up before you record

```bash
.venv/bin/python -m scripts.replay.generate runs/market.db docs
.venv/bin/python -m scripts.serve --merchant m_horizon --port 8795
```

Open two tabs:

| tab | page | who it is for |
|---|---|---|
| 1 | `localhost:8795/m-horizon.html` | **the merchant** |
| 2 | `localhost:8795/desk.html` (passcode `razorpay`) | **Razorpay** |

Put them side by side if you can. The whole point is that they move together.

---

## 1 · The problem worth solving  (0:00 – 0:40)

**Screen:** the front page, `localhost:8795/index.html`.

> Razorpay already moves money between millions of businesses. Which means
> Razorpay already knows something no one else on earth knows: **who buys
> what, from whom, and at what price.**
>
> That data has only ever been used to move a payment from A to B. It has
> never been used to introduce A to B.
>
> So a café in Koramangala overpays for cold brew, while a supplier eight
> kilometres away sits on stock. They never meet. Not because the market is
> efficient — because nobody built the thing that connects them.
>
> We built it. Every Razorpay merchant gets an agent. The agents find each
> other, negotiate, and settle on real Razorpay payments.

---

## 2 · What actually happened  (0:40 – 1:20)

**Screen:** the four figures, then hover the network ring.

> This is a real run. **35 businesses. ₹145,434 settled between their agents.
> 30 trading partnerships that nobody introduced. 1,310 events on the audit
> trail.**
>
> Every one of those relationships is new. No sales team, no directory, no
> marketplace listing fee. The agents found each other because Razorpay could
> see both sides.

**Pause on the ring.**

> **This is the moat.** A startup could build these agents. It could not build
> this network, because it has no merchants and no visibility into what they
> buy. Razorpay starts with both on day one. **The distribution is already
> paid for.**

---

## 3 · One merchant asks for something  (1:20 – 3:10)  ← *the centrepiece*

**Screen:** tab 1, Horizon. Point at the zeroes.

> This business signed up today. ₹0 committed. Nothing on its books. Four
> agents that have never done anything.

**Click "Ask for something". Type slowly:**

    cold brew concentrate 200 units under 300 each

> No form. No supplier directory. No RFQ. I type what I need the way I would
> say it to a person — including the quantity and what I am willing to pay.

**Press Find it. Now switch to tab 2 — the Razorpay desk — and leave it there
for a beat.**

> And here is the same moment on Razorpay's side.

*(Point at the amber banner: **horizon is buying something right now**.)*

> **This is happening now.** Its agent is out on the order book. The merchant
> sees plain English; Razorpay sees the event numbers and the actor ids. Same
> trade, same second, two audiences.

**Switch back and forth as the stages land.** Nothing is pacing this — a stage
appears when the event behind it is written, so what you are watching is how
long it actually takes. About twelve seconds end to end.

| the merchant sees | Razorpay sees |
|---|---|
| Posting what you need · 200 units, at most ₹300 each | `1348  posted what it needs` |
| Finding who can supply it · **packmate**, from 3 candidates | `1349  picked who to deal with` |
| Checking it against your limits · **allowed** | `1351  the gate ruled` |
| Paying · **₹4,980** · on a real Razorpay order | `1352  money committed · order_…` |

> The merchant never sees an agent id or a raw event. It sees what a shopkeeper
> needs to know. Everything underneath is still there, still numbered, and
> Razorpay can read all of it.

**If the gate refuses** — it often does with a supplier this merchant has never
dealt with — rest on that step:

> **The gate refused it.** New supplier, amount over the cap for a first deal
> with someone you have never traded with. So the agent came back smaller, and
> that went through.
>
> **The refusal is written down** — not just the outcome, the ruling itself,
> before any money moved, whether it says yes or no. **78 rulings in this run,
> 25 of them refusals.**
>
> That is what makes this sellable to a business. Nobody hands an AI their bank
> account on a promise of good behaviour.

**Then reload the merchant page.**

> And the zeroes are gone. That trade is on its rail now, under Where the money
> went, with the event numbers to look any of it up.

## 4 · Where the revenue comes from  (3:10 – 4:20)  ← *the money slide*

**Screen:** tab 1 → Where the money went → the cold brew trade.

> Now the number that matters to a merchant.
>
> The seller was asking **₹210** a unit. The agent settled at **₹195**. These
> are the actual sentences the two agents said to each other — not a summary,
> the negotiation.

**Scroll the offers.**

> Fifteen rupees a unit. Small. Now look at it across the whole run.
>
> **On ₹145,434 of buying, the agents took ₹11,169 off the asking price.
> That is 7.7 percent — and it is not a discount anybody gave them. It is
> margin they took, on money the merchant was going to spend anyway.**
>
> For a business on thin margins, that is not a saving. **That is the year.**
>
> And it costs the merchant nothing to get: no procurement hire, no time on
> the phone, no negotiation skill. Their agent does it while they are asleep.

**Then, briefly:**

> There is a second revenue line, and it is the one nobody expects. Every
> business here is also a **seller**. Horizon lists five things. 175 listings
> across the exchange, and every one is searchable by 35 other businesses'
> agents. A merchant does not just buy cheaper — **it gets found.**

---

## 5 · The thing only Razorpay can sell  (4:20 – 5:20)  ← *the business model*

**Screen:** dropdown → **Bl Thirdwave** → scroll to "What your category clears
at".

> Cold brew **clears at ₹195**, against a **₹210** asking price. Half of all
> buyers get a discount. So if you are paying the ask, you are leaving money on
> the table, and you should push.
>
> Electronics assembly closes at the asking price every single time. So do not
> push there — you will spend the one thing an agent cannot buy more of, which
> is the other side's patience.

**Switch the dropdown to Packmate. Same card. No numbers.**

> This merchant is on the Standard plan. It is told a gap exists. It is not
> told where.
>
> **That is a Razorpay plan tier.** And the lock is real — those figures were
> never rendered into this page. You can open developer tools on camera.

**Then the line to land:**

> A seller only sees its own prices. A buyer only sees its own bills. **Only
> the company that settles both sides of every trade can say what the middle
> is.** Not Amazon, not IndiaMART, not a startup. Razorpay, and nobody else.
>
> So this is not a feature that helps merchants. **It is a product Razorpay can
> charge for, built out of exhaust it already owns**, and it gets better with
> every merchant who joins — which is the definition of a moat.

---

## 6 · It fails safely, which is why it can ship  (5:20 – 6:10)

**Screen:** back to the trade rail, the red stations.

> Then it broke.
>
> Razorpay said a payment was captured. Our books said pending. Nobody was
> watching — the reconciliation agent found it on a routine sweep.
>
> It froze **that one merchant**, because one business's disagreement must not
> stop everybody else. It repaired the settlement using the payment id
> **Razorpay gave back** — not one we invented, because a repair that invents
> an id is a machine lying about money. Then it let the merchant trade again,
> because a hold that never lifts is a ban.
>
> **21 mismatches caught and repaired this way. No human touched any of them.**

**Screen:** the books card → Open your tab in Google Sheets.

> And the books keep themselves. Every business gets its own tab, written from
> the same audit trail. A merchant does not do bookkeeping for this — it reads
> it.

---

## 7 · Why this is realistic, not a pitch  (6:10 – 6:50)

**Screen:** the desk, ledger running.

> Everything you have seen runs today on **Razorpay's existing test-mode
> APIs**. Orders, payment links, notes on a transaction. No new rails, no new
> compliance surface, no new money movement — every rupee still travels the
> path Razorpay already operates and already audits.
>
> The agent is a layer on top. The gate is a policy check before an existing
> API call. The audit trail is an append-only log. **There is nothing here
> that needs a licence Razorpay does not already hold.**
>
> **1,310 events. 742 tests. One command to run the whole thing.**

---

## 8 · Close  (6:50 – 7:00)

**Screen:** the front page.

> So, for a merchant: it finds them suppliers they would never have met, takes
> roughly eight percent off what they were going to spend anyway, gets their
> own catalogue in front of every other business on the network, keeps their
> books, and refuses anything outside the limits they set — in writing.
>
> For Razorpay: a new paid tier, sold out of data it already owns and nobody
> else can assemble, on rails it already runs.
>
> **Payments told you a transaction happened. This tells you who you should be
> doing business with — and proves every rupee of it.**

---

## The numbers, if you are asked

| | |
|---|---|
| Settled between agents | **₹145,434** |
| Taken off the asking price | **₹11,169 — 7.7% of everything bought** |
| Businesses on the exchange | 35 |
| Listings searchable by every agent | 175 |
| Partnerships nobody introduced | 30 |
| Gate rulings recorded | 78, of which **25 refused** |
| Book mismatches caught and repaired | 21 |
| Events on the audit trail | 1,310 |
| Tests | 742 |
| Humans in the loop | **0** |

## Say it this way, not that way

| don't say | say |
|---|---|
| "AI agents negotiate autonomously" | "your agent argues the price down while you sleep" |
| "event-sourced audit log" | "every decision is written down, in order, with a number you can look up" |
| "policy engine with configurable thresholds" | "it refuses anything over your limit, and writes down why" |
| "cross-merchant aggregated intelligence" | "what your category actually sells for — which only Razorpay can see" |
| "conversion uplift" | "₹11,169 off ₹145,000 of buying" |

## Three things to know while recording

**The replay is a real trade, not a script.** It is a correlation id out of the
log with its own event numbers, played back on a clock. Say *"this is a trade
that happened, replayed so you can watch it"* — it is repeatable, which a live
buy is not, and every number in it can be looked up afterwards.

**Rehearse on `m_daybreak`, never on Horizon.** Asking posts a real order, so a
rehearsal spends the empty page you are recording.

**The pay link will be missing.** The Razorpay test account is at its 30-link
cap, so a new trade returns a real order id and no clickable link. Say *"on a
real Razorpay order"* and show the order id — that is what the money moved on.
