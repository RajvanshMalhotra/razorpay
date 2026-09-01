# The Agent Exchange

**Every Razorpay merchant gets an AI agent that finds who to trade with, argues the
price, settles on real Razorpay payments, and keeps the books — with every rupee
ruled on before it moves.**

Built solo for the Razorpay hackathon, **Track 01 — AI Growth & Agentic Commerce**.

> The track's bar: *every money action explainable, bounded and gated; show the audit
> trail and one failure handled gracefully.* That is not a feature bolted on here — it
> is the architecture. Nothing moves money without first writing down why it was
> allowed to.

---

## The problem

Every business needs suppliers and needs buyers. Finding them, checking whether they
are real, and arguing the price is work a small business does not have the hours for.
So most trade with whoever they already know, at whatever price is listed — not
because it is the best deal, but because searching costs more than the saving.

Razorpay already sits between these businesses and already knows who ships, who pays,
and who pays late. Nobody has used that position to make an introduction.

## What it does

One agent per merchant, split into four parts with separate memories. Three act, one
only watches:

| Part | Does | Knows |
|---|---|---|
| **Trader** | Buys and sells | Inventory, needs, counterparty terms |
| **Scout** | Watches trends, bids on intelligence | What is rising, what is worth points |
| **Diplomat** | Holds relationships — advises, never vetoes | Who we know, how it went |
| **Subconscious** | Never acts; files durable lessons | How every past deal actually went |

Plus three house agents that belong to Razorpay rather than to any merchant: a
**research desk** that ranks trending campaigns across the whole client base, an
**accountant** that reconciles against Razorpay and repairs drift, and an
**auctioneer** that sells market intelligence by sealed second-price bid.

---

## The run

Every figure below is read from `runs/market.db` and is recomputable by anyone who
dumps the same events.

| | |
|---|---|
| Recorded events | **986** |
| Merchant brokers | **32** |
| Negotiations / rounds | **67** / 180 (2.7 avg) |
| Committed on Razorpay orders | **₹1,39,884** |
| Settled and confirmed | **₹73,836** |
| Real Razorpay test-mode order ids | **32** |
| Gate rulings — allowed / refused | **49 / 25** |
| Drifts detected / repaired | **21 / 17** |
| Freezes / resumes | **1 / 1** |
| Humans involved | **0** |
| Tests | **676** |

### One trade, end to end

A Bangalore coffee business needs cold brew concentrate. One correlation id threads
the whole story, and every step carries the sequence number of the event it came from.

| Event | What happened |
|---|---|
| `205` | Posts a descriptive bid: 160 units of cold brew concentrate |
| `217` | Shortlists three sellers and argues for one, in writing — ₹210/unit |
| `256` | Agreed at ₹195/unit, down ₹15 from the seller's own ask |
| `258` | **The gate refuses it.** ₹31,200 exceeds the ₹20,000 per-payment cap |
| `259` | Comes back smaller — 25 units, *same price*. The log calls it "trial size 25 of 160" |
| `260` | **The gate rules again — ALLOW.** Also under the ₹5,000 unknown-counterparty cap |
| `261` | Pays ₹4,875 on a real Razorpay order, settlement `stl_87b9f1b75d0b` |

The refusal is written down **before** any money moves. The ruling is the record, not
the outcome.

### The failure, handled

That same settlement is the one that breaks — not a declined card, but a silent
consistency failure between two systems, which is the kind that actually costs
businesses money because nobody is watching for it.

| Event | What happened |
|---|---|
| `763` | Razorpay says captured, our books say pending. Found on a routine sweep |
| `936` | Freezes **that one merchant**, not the market |
| `937` | Repairs it using `pay_TV9vxLLDM2PvgD` — Razorpay's id, never one we invented |
| `938` | Lets the merchant trade again. A freeze that never lifts is a ban, not a hold |

17 of 21 drifts were repaired this way with no human involved. The other 4 could not
be confirmed because Razorpay rate-limited the sweep, and the system recorded **12
failed checks** rather than guessing. An unconfirmed settlement stays unconfirmed.

---

## How it is built

Python, ~15,000 lines, 676 tests. Three properties do the load-bearing work:

**One append-only event log**, enforced by a database trigger. Order books, point
balances, the relationship graph and every page in the replay are *projections* of it,
computed by folding the log — never stored alongside it and never edited.

**A gate that logs the yes.** Every money action emits a `POLICY_DECIDED` event before
it executes, including when the verdict is ALLOW. That is the difference between a
gate and a wrapper.

**Sources that cannot become figures.** The ranking is arithmetic over the log. The
press and the Reddit discussion attached to each row run *after* the ranking is fixed
and can only ever add text — guarded by a test that asserts the ranking function
cannot reach the model, the news, or Reddit.

### Running it

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env          # add your Razorpay TEST-mode keys
.venv/bin/python -m pytest -q

.venv/bin/python -m scripts.market.run          # run a market
.venv/bin/python -m scripts.market.research     # publish the campaign board
.venv/bin/python -m scripts.market.radar        # publish the brand radar
.venv/bin/python -m scripts.replay.generate     # build the replay pages
```

The config **refuses to start** on a key that does not begin `rzp_test_`. Not a
warning — it raises and the run stops.

### Optional keys, each one adding a source

Everything above runs with no keys but the Razorpay ones. The research agents get
better as you add more, and every result records which sources it was actually
built from — a board built from one source says so.

| Add to `.env` | What it buys |
|---|---|
| *(nothing)* | Reddit over public RSS. Throttled, and it reports no upvote counts. |
| `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` | Reddit's own API. Free script app, no throttling. |
| `SOCIALCRAWL_API_KEY` | The above plus X, on 100 free credits. Upvotes and comment counts. |
| `X_BEARER_TOKEN` | X directly, for anyone who has paid for it. |

### Layout

```
src/exchange/          the core: event log, matching, policy gate, settlement rails
src/exchange/agents/   the merchant broker and its four parts
src/exchange/house/    research desk, accountant, auctions, campaign board, Reddit
scripts/market/        running a market, reconciling, publishing, Sheets sync
scripts/replay/        generating the replay pages from a finished log
docs/                  the generated replay — landing page, 32 merchant pages, desk
```

### Deliberately not here

No autonomous outreach that joins Discord or Telegram servers or messages founders. It
breaks those platforms' terms, reads as a spambot, and cuts directly against the
"bounded and gated" bar this is built to meet. Growth signal comes from public
read-only sources; anything outbound stays human-approved.
