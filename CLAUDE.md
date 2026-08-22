# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

**An exchange where every Razorpay merchant has an AI broker agent that trades with other merchants' broker agents.**

Built for Razorpay hackathon **Track 01 — AI Growth & Agentic Commerce**.

- **Deadline:** 2026-09-04 (project started 2026-08-22 — 13 days)
- **Team:** solo
- **Submission:** recorded video, not a live demo
- **Status:** design complete, no code written yet

Track's bar, which every design decision serves:
> Every money action explainable, bounded and gated. Show the audit trail and one failure handled gracefully.

## The product in one paragraph

Every merchant gets a representative broker agent. Brokers find each other, negotiate, and settle real trades on **Razorpay test-mode APIs**. Separately, Razorpay runs a **house research agent** that mines aggregate cross-merchant activity into market intelligence — *"cold brew is rising in Bangalore," "a skincare brand beat category conversion 3.2x"* — publishes free headlines to a **victory feed**, and **auctions the details**. Brokers pay for intel with **points**, not cash. Points are earned by trading well and are convertible to Razorpay fee rebates. When your own win is bought by someone else, you earn points too.

**The pitch line:** *Razorpay is the platform. Merchants are the creators. Their wins are the content. Other merchants pay to watch. Revenue is shared.* — YouTube, for business intelligence.

## Architecture

### Per merchant: one broker, four parts

Three parts act, each with an **isolated context window**, distilling short summaries upward to the orchestrator. One part only remembers.

| Part | Does | Knows |
|---|---|---|
| **Trader** | Buys and sells | Inventory, needs, counterparty terms, open orders |
| **Scout** | Watches trends, bids on intel | What's rising, bid history, what's worth points |
| **Diplomat** | Handles relationships | Who we know, how it went, who to introduce |
| **Subconscious** | Observes everything, never acts | How every past deal actually went |

**Why contexts are isolated:** a single agent holding everything gets slow, gets confused, and leaks the wrong fact into the wrong conversation (quoting what we paid supplier A while negotiating with supplier B). Each sub-agent reports a summary up, never its full contents.

**The Subconscious** is the differentiator. It consumes this merchant's event stream, consolidates episodes into durable lessons, and injects relevant recall into a sub-agent's context *before* it acts:
> *Dealt with merchant_41 three times. They push hard on delivery dates. Paid nine days late once. Said yes fast to a volume discount. Don't open on price.*

### Exchange-level agents

- **House research agent** — mints `InsightLot`s from aggregate merchant activity, publishes the victory feed, runs auctions. Never buys.
- **Clearing & Audit agent (the accountant)** — reconciles local settlement records against Razorpay test-mode state, asserts invariants, mints points, freezes agents on drift. Exchange-level rather than per-merchant because reconciliation needs both sides of every trade and point conservation is a global invariant; its per-merchant statement is exposed into each orchestrator's context.

## Core design decisions

**1. One order book, two currencies, two rails.**
`Order` is unified: `side` (BID/ASK), `qty`, `limit_price`, `currency`, `expires_at`. `currency ∈ {INR, CREDITS}` routes settlement — INR through Razorpay test-mode order → payment → capture; CREDITS as an atomic ledger transfer. Separate policy regimes, so competitive bidding wars can only happen in play money.

**2. Bids may be descriptive, not just referential.**
An order carries either `asset_ref` (a specific listing) or `asset_query` (natural language need + structured constraints). Descriptive bids are resolved by **hybrid retrieval — BM25 + embeddings, reciprocal-rank-fused**. Same code path serves the Trader hunting supply and the human-facing storefront.

**3. The event log is the audit trail AND the replay source.**
Append-only: `event_id, seq, ts, actor, type, payload, causation_id, correlation_id`. Order book state, point balances, and the relationship graph are all **projections** of it. One `correlation_id` threads a complete story: trend captured → bid posted → match → negotiation → policy decision → capture → receipt.

**4. Every money action emits a `PolicyDecision` before it happens.**
`verdict ∈ {ALLOW, DENY, REQUIRE_HUMAN}` plus `reason` and the limits evaluated. A separate logged record, not a wrapper around the payment call — so the audit trail shows the gate firing even when the answer is yes.

**5. Insight auctions clear second-price.**
Sealed bids, highest wins, pays runner-up's price. `sort(bids); winner = bids[0]; price = bids[1]`. Makes honest valuation the dominant strategy, so agents reason about worth instead of guessing rivals — which keeps their on-screen reasoning legible. Minor detail; free to implement; not a pillar.

**6. Points reward broker skill, not volume.**
Margin captured against ask, fill rate, counterparty reliability; volume is a minor term. Volume-weighting would make the biggest merchant win by round three and kill the demo. Needs a damper (decay or tier caps) so the flywheel can't run away. Minted only by the accountant.

**7. Anti-incumbency: explore, bounded.**
Relationship history creates rich-get-richer bias that would freeze the market into cliques and starve new merchants. Three rules:
- Unknown counterparties are scored **optimistically**, so the Diplomat actively wants to try them.
- New counterparties get a **low spending cap**, enforced by the existing policy gate. Cap rises with track record.
- A single bad deal barely moves the score. The Subconscious distinguishes *"haggled hard"* (fine) from *"didn't deliver"* (serious).

Structural rule: **the Diplomat advises, it never vetoes.** The orchestrator decides.

**8. Privacy floor on intel.**
Opt-in by plan; always anonymized; never derived from fewer than N merchants; contributor earns points when their win is bought. The privacy check is a visible, logged step — a good thing to fail loudly on camera.

## Build approach

**Offline market, replay visualization.** A recorded video means the exchange is allowed to be slow. Run the real agents offline for hours, write every event to the log, then build a replay UI and shoot the video against it at speed.

Buys real emergent behavior at zero demo risk; lets you run the sim repeatedly and use the best run; and if asked "is this staged?", the answer is a real log with real Razorpay test-mode payment IDs. Also decouples development cleanly for a solo build: engine first, then visualization against a frozen log format.

Rejected: live-driven recording (hostage to sampling luck, unwatchable dead air) and a scripted two-act demo (the "stock market for agents" claim doesn't survive it).

### Schedule

| Days | Build |
|---|---|
| 1–3 | Exchange core: asset/order model, matching engine, event log, Razorpay test-mode settlement, policy gate |
| 4–6 | Merchant broker: orchestrator + isolated sub-agent contexts, memory/context engine, Subconscious, relationship graph |
| 7–8 | House research agent: trend ingestion, insight lots, privacy floor, sealed-bid auction, point ledger, accountant |
| 9–10 | Run the market, tune the economy until a run is *interesting*, wire real Reddit/trend data |
| 11–12 | Replay visualization — what judges actually watch, gets real time |
| 13 | Record, edit, submit |

## The video (the spine of the whole build)

One trade, one `correlation_id`, followed end to end.

1. **Scout** reads real Reddit/trend data, spots *"vitamin C serum under ₹800"* recurring.
2. **Subconscious** whispers: *"last time we chased skincare we were two weeks late — go faster."*
3. Victory feed lot: *"a skincare brand beat category conversion 3.2x."* Scout bids points, wins the auction, gets the playbook.
4. Orchestrator combines demand + playbook + inventory: we're short on packaging.
5. **Trader** posts a descriptive bid: *500 eco packaging, ≤₹22/unit, by Friday.*
6. Hybrid retrieval surfaces three sellers.
7. **Diplomat** advises on each; the agent deliberately tries the **unknown** merchant with a small cap.
8. Traders negotiate, bounded rounds, settle at ₹19.40.
9. **Policy gate** fires and logs its reasoning before any money moves.
10. Razorpay test-mode order → payment → capture. Real payment ID on screen.
11. **The failure:** capture doesn't land. Accountant catches the mismatch, freezes the agent, repairs from the event log, resumes. *This is the track's required "one failure handled gracefully" — deliberately not a declined card.*
12. Books close. Points minted. Relationship updated. Subconscious files the lesson.
13. **The easter egg:** a human types *"I need biodegradable mailers under ₹15"* into the dashboard — same machinery, person driving, human approves, money moves.

## Scope guardrails

- **In scope:** the exchange, the four-part broker, the house agent + victory feed + auctions, the accountant, the point economy, hybrid retrieval, the policy gate, the replay UI, the NL input box on the dashboard.
- **Deliberately cut:** autonomous outreach that joins Discord/Telegram/WhatsApp servers or DMs founders on Reddit/Instagram. It violates those platforms' ToS, reads as a spambot on camera, and cuts against the "bounded and gated" bar. Growth signal comes from **public read-only sources**; anything outbound is **human-approved**.
- The natural-language storefront is **not** a second product surface — it's one input box writing a descriptive bid to the existing order book.

## Working notes

- Read `chat history.md` for the full design conversation and the reasoning behind each decision.
- The user asked twice for **plain language over jargon**. Explain mechanisms concretely with worked examples; don't pile on theory. If something is a minor implementation detail, say so rather than presenting it as a pillar.
- The user catches design flaws well (they spotted the incumbency-bias problem unprompted). Surface real trade-offs rather than smoothing them over.
