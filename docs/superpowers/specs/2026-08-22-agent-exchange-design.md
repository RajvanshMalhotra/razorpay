# Design: The Agent Exchange

**Date:** 2026-08-22
**Deadline:** 2026-09-04 (13 days, solo)
**Submission:** recorded video
**Track:** Razorpay Hackathon Track 01 — AI Growth & Agentic Commerce

---

## 1. Goal

Build an exchange where every Razorpay merchant is represented by an AI broker agent. Brokers discover each other, negotiate, and settle real trades on Razorpay test-mode APIs. Alongside it, Razorpay operates a house research agent that turns aggregate cross-merchant activity into market intelligence and auctions it to those brokers for points.

The track's bar drives every decision below:

> Every money action explainable, bounded and gated. Show the audit trail and one failure handled gracefully.

**Success criteria**

1. A complete trade runs end to end — signal → bid → match → negotiation → policy gate → Razorpay capture → receipt — reconstructable from a single event log thread.
2. Every money action is preceded by a logged `PolicyDecision` with an explicit reason and the limits it evaluated.
3. One failure (a capture that doesn't land) is detected by the accountant, contained, repaired, and resumed — visibly.
4. A human can type a need in plain language and drive the same machinery to a settled payment.
5. The whole thing is legible on video in under six minutes.

## 2. Non-goals

- **Autonomous outreach on Reddit / Instagram / Telegram / Discord / WhatsApp.** Cut deliberately: joining servers and DMing founders violates those platforms' terms, reads as a spambot on camera, and contradicts the "bounded and gated" bar. Growth signal is drawn from public read-only sources only; anything outbound is human-approved.
- **Real money.** Razorpay test mode throughout. Points are internal and never settle to cash — they convert to fee rebates.
- **Production auth, multi-tenancy, or deployment.** Single-process simulation plus a replay UI.
- **A second storefront product.** The natural-language input box writes a descriptive bid to the existing order book. It is not a separate surface.

## 3. Domain model

### Actors

```
Actor
  actor_id           str
  kind               MERCHANT | HOUSE | ACCOUNTANT | HUMAN
  merchant_id        str | None      # Razorpay test account
  plan_tier          str
  status             ACTIVE | FROZEN # accountant can freeze
```

### Assets

```
Asset
  asset_id           str
  kind               GOODS | SERVICE | INSIGHT
  title              str
  spec               dict            # typed per kind
  currency           INR | CREDITS
  origin_actor_id    str
```

`INSIGHT` assets are mintable **only** by the house agent, and always carry `currency = CREDITS`. `GOODS` and `SERVICE` always carry `currency = INR`. This invariant is asserted by the accountant.

`InsightLot.spec` additionally carries:
```
  headline           str    # public, free — the victory feed entry
  playbook           dict   # private, auctioned — channel, creative, spend, audience, timing
  contributor_ids    [str]  # merchants whose activity it derives from
  k                  int    # aggregation count; must be >= K_MIN
  category           str
```

### Orders — one book, two currencies

```
Order
  order_id           str
  actor_id           str
  side               BID | ASK
  asset_ref          str | None      # a specific listing
  asset_query        dict | None     # natural-language need + structured constraints
  qty                int
  limit_price        int             # minor units (paise) or points
  currency           INR | CREDITS
  expires_at         ts
  policy_snapshot    dict            # caps in force when posted
```

Exactly one of `asset_ref` / `asset_query` is set. Descriptive orders (`asset_query`) are what hybrid retrieval resolves — and are what the human-facing input box produces.

### Matching, negotiation, settlement

```
Match
  match_id, bid_order_id, ask_order_id, clearing_price, score, rationale

Negotiation
  negotiation_id, match_id, rounds[], max_rounds, outcome
  # round: {proposer, terms, message, ts}

Settlement
  settlement_id, match_id, currency, amount, status
  razorpay_order_id, razorpay_payment_id      # INR rail only
  reconciled_at, reconciliation_status

PolicyDecision
  decision_id, action_ref, actor_id, verdict, reason, limits_evaluated, ts
  # verdict: ALLOW | DENY | REQUIRE_HUMAN

CreditLedgerEntry
  entry_id, actor_id, delta, reason, source_settlement_id, ts

RelationshipEdge
  from_actor_id, to_actor_id
  deals_count, total_value, reliability_score, confidence
  last_interaction_at, lessons_ref
```

### The event log

Append-only, the single source of truth. Every table above is a **projection** of it.

```
Event
  event_id, seq, ts, actor_id, type, payload
  causation_id       # the event that directly caused this one
  correlation_id     # the whole story this belongs to
```

One `correlation_id` threads: *trend captured → insight won → bid posted → match → negotiation rounds → policy decision → Razorpay capture → receipt → points minted → lesson filed.* This single field is what makes the replay UI able to highlight one trade while the rest of the market moves behind it, and it is what makes the audit trail navigable.

## 4. The merchant broker

One orchestrator, three acting sub-agents with **isolated context windows**, one passive memory layer.

| Part | Acts? | Context holds | Distills upward |
|---|---|---|---|
| **Trader** | yes | Inventory, needs, counterparty terms, open orders | *"Short on X; merchant_41 quotes best"* |
| **Scout** | yes | Trend feeds, captured signals, bid history | *"Vitamin-C demand spiking; lot worth ~1,850"* |
| **Diplomat** | yes | Who we've dealt with, how it went, what they care about | *"41 reliable, warm to 09; try the unknown small"* |
| **Subconscious** | no | Every episode this merchant has lived | injects recall before each action |

### Why isolation

A single agent holding all three concerns degrades three ways: context grows until reasoning gets slow and lossy; irrelevant detail crowds out the decision at hand; and facts leak across boundaries — quoting supplier A's price while negotiating with supplier B. Each sub-agent returns a **short structured summary** to the orchestrator, never its raw context.

### The Subconscious

Consumes this merchant's event stream. Two operations:

- **Consolidate** — after each completed episode (a negotiation, a settlement, an auction), distill it into a durable lesson keyed by counterparty, category, and outcome type. Episodic → semantic.
- **Recall** — before a sub-agent acts, retrieve the lessons relevant to that action and inject them into its context.

```
Dealt with merchant_41 three times. They push hard on delivery dates.
Paid nine days late once. Said yes fast to a volume discount.
Don't open on price.
```

Lessons distinguish **behavioural** observations (*haggles hard* — neutral) from **reliability** observations (*didn't deliver* — costly). Only the latter meaningfully moves `reliability_score`.

## 5. Exchange mechanics

### Matching — goods and services

1. Descriptive bid arrives (`asset_query`).
2. **Hybrid retrieval** over open ASKs: BM25 over title/spec text (catches exact SKU, brand, material terms) fused with dense embedding similarity (catches intent paraphrase), combined by reciprocal rank fusion.
3. Feasibility filter: price ≤ `limit_price`, qty available, delivery within constraint.
4. Rank by score, with the Diplomat's counterparty advice as a **soft term, never a filter**.
5. Top-K candidates go to negotiation.

### Negotiation

Two Trader agents exchange bounded rounds (`max_rounds`, default 4). Each round proposes terms with a short rationale. Terminates on agreement, on refusal, or on round exhaustion — exhaustion is a normal outcome, not an error. The round cap is itself a gate: an agent cannot burn unbounded tokens or spiral.

### Insight auctions

Sealed-bid, second-price. All bids submitted blind; highest bidder wins and pays the second-highest bid.

```python
bids.sort(key=lambda b: b.amount, reverse=True)
winner, price = bids[0], bids[1].amount
```

Rationale: under first-price, agents shade bids by guessing rivals, which makes their reasoning both unstable and unreadable. Under second-price, bidding true value is dominant, so on-screen reasoning is about worth rather than mind-reading. Implementation cost is negligible; this is a detail, not a pillar.

**Privacy floor**, checked and logged before any lot is listed:
- `k >= K_MIN` (default 25) contributing merchants
- no output attributable to a single merchant
- contributors are opted in at plan level

A failed privacy check emits a `DENY` decision and the lot is never listed.

### Anti-incumbency

Relationship history creates rich-get-richer bias: known counterparties win business, accrue history, get preferred harder; newcomers never get a first deal. This would freeze the market into cliques, starve new Razorpay merchants, and make the demo static by round three.

Three rules:

1. **Optimism under uncertainty.** An unknown counterparty is scored *above* the population mean, not below, until evidence arrives. The Diplomat actively wants to try them.
2. **Trial-size bounding.** Counterparties with low `confidence` get a low per-transaction cap, enforced by the policy gate. Cap rises with track record. *Trade with anyone; risk little on strangers.*
3. **Evidence-weighted updates.** A single data point barely moves `reliability_score`; it widens rather than collapses the estimate. Behavioural vs. reliability failures are weighted differently.

Structural: **the Diplomat advises, it never vetoes.** The orchestrator decides.

## 6. Money

### Two rails

| | INR rail | CREDITS rail |
|---|---|---|
| Assets | GOODS, SERVICE | INSIGHT |
| Settlement | Razorpay test-mode order → payment → capture | atomic ledger transfer |
| Policy regime | spend caps, per-txn limits, counterparty caps, human-approval threshold | bid caps, balance check |
| Blast radius | test-mode money | play money |

Competitive bidding pressure lives entirely on the CREDITS rail, so an over-eager agent cannot cause real harm.

### The policy gate

Every money action emits a `PolicyDecision` **before** it executes — a separate logged record, not a wrapper around the payment call, so the audit trail shows the gate firing even when the answer is yes.

Limits evaluated:
- per-transaction cap
- rolling spend window cap
- counterparty exposure cap (scaled by `confidence` — see anti-incumbency)
- `REQUIRE_HUMAN` threshold above a configured amount
- actor `status != FROZEN`

`REQUIRE_HUMAN` suspends the action pending approval and emits an event. This is the same gate the human-facing input box passes through.

### Point economy

**Earned** — minted only by the accountant, from settled INR activity:
- margin captured against ask (primary)
- fill rate
- counterparty reliability delivered
- volume (minor term)
- **contributor royalty** — when an insight lot derived from your activity is bought, you earn a share

Volume-weighting was rejected: it makes the largest merchant win by round three and kills both the economy and the demo. A small merchant that negotiates sharply must out-earn a large one that overpays.

**Spent** — insight auctions.

**Damper** — balances decay slowly, and per-tier caps bound accumulation, so the flywheel cannot run away.

**Convertible** — points convert to Razorpay fee rebates. This makes them monetizable profit rather than a closed loop, costs Razorpay margin instead of cash, and requires no payout rail.

## 7. The accountant (Clearing & Audit agent)

Exchange-level, not per-merchant: reconciliation needs both sides of every trade and point conservation is a global invariant. Its per-merchant statement is projected into each orchestrator's context, so a broker still "sees its own books."

Runs on a periodic pass:

1. **Reconcile** — fetch Razorpay test-mode payment state, compare against local `Settlement` records. Detects the dropped webhook: captured upstream, still `pending` locally (and the inverse).
2. **Assert invariants** — points conserved and minted only by the accountant; no `Settlement` without a preceding `ALLOW` decision; no orphaned matches; asset kind ↔ currency consistency.
3. **Mint points** — the earnings formula lives here alone.
4. **Halt on drift** — set actor `status = FROZEN`, emit an event, block further trading until repaired from the event log.

## 8. The failure demo

Deliberately **not** a declined card — every team will show one.

1. A capture succeeds at Razorpay but the local record stays `pending` (simulated dropped webhook).
2. Accountant's next pass detects the mismatch.
3. Emits a drift event; freezes the merchant mid-trade. Trading visibly stops.
4. Repairs local state by replaying from the event log plus authoritative Razorpay state.
5. Unfreezes; the in-flight trade resumes and completes.

Every step is in the audit trail and visible in the replay UI.

## 9. Growth signal

Public, read-only sources only. Candidates: Reddit (PRAW, public subreddit reads), Google Trends (pytrends), and a category keyword feed. Raw signal, not model-generated speculation — the Scout summarizes real retrieved posts and trend series, and every captured signal keeps a source reference so it is verifiable on camera.

Fetches are cached to disk so simulation runs are reproducible and rate limits are never a demo risk.

## 10. Replay & visualization

The simulation runs offline and slow; the video is a replay of the resulting event log at speed.

- **Log format is frozen early** — the engine and the UI are developed against it independently. This matters for a solo build.
- The UI reads a log file, reconstructs projections by folding events, and scrubs through time.
- One `correlation_id` can be pinned; the pinned trade is highlighted while the rest of the market animates behind it.
- Panels: order book, victory feed + live auction, the four-part broker with each context visible, policy decisions, the ledger, and the accountant's reconciliation status.

This buys real emergent behavior at zero demo risk, allows re-running until a run is genuinely interesting, and answers "is this staged?" with a real log containing real Razorpay test-mode payment IDs.

## 11. Proposed stack

*(Needs confirmation before day 1.)*

- **Python 3.11+** — best fit for the agent, retrieval, and data-source ecosystem
- **Razorpay Python SDK** — test mode
- **SQLite** — event log + projections; single file, trivially shippable with the submission
- **rank_bm25** + **sentence-transformers** — hybrid retrieval
- **PRAW** / **pytrends** — growth signal, disk-cached
- **FastAPI** — serves the log and the dashboard
- **Vanilla TS or React** — replay UI

## 12. Testing

- **Unit** — matching, RRF fusion, second-price clearing, policy gate limits, point formula, reliability updates.
- **Property** — points conserved across arbitrary event sequences; no settlement without a preceding `ALLOW`; projections folded from the log always equal live state.
- **Integration** — full trade against Razorpay test mode; the drift-detect-freeze-repair-resume path; the human descriptive-bid path.
- **Simulation smoke** — an N-merchant run completes without invariant violation and produces a non-degenerate market (more than one merchant trades; no single agent holds everything).

## 13. Schedule

| Days | Build |
|---|---|
| 1–3 | Exchange core: domain model, event log, matching engine, Razorpay settlement, policy gate |
| 4–6 | Broker: orchestrator, three isolated contexts, Subconscious, relationship graph |
| 7–8 | House agent, victory feed, auctions, privacy floor, point ledger, accountant |
| 9–10 | Wire real growth signal; run and tune the market until a run is interesting |
| 11–12 | Replay visualization |
| 13 | Record, edit, submit |

## 14. Risks

| Risk | Mitigation |
|---|---|
| Agent negotiation produces boring or degenerate runs | Offline runs are cheap and repeatable; tune the economy on days 9–10 with explicit non-degeneracy checks |
| Solo build overruns into the video days | Log format frozen by day 3 so the UI can proceed independently; UI days are protected |
| LLM cost across many agents × many rounds | Bounded negotiation rounds; small models for sub-agents; cached signal fetches |
| Razorpay test-mode behaviour differs from assumptions | Validate the order → payment → capture path on day 1, before anything is built on it |
| Scope creep back toward outreach | Recorded as an explicit non-goal in §2 |

## 15. Open questions

1. **Stack confirmation** (§11) — needed before day 1.
2. **Merchant count for the simulation.** Recommend 6–8: enough for a live-looking market and for anti-incumbency to be visible, few enough to stay cheap and legible on screen.
3. **Whether the human approval step in the video is real UI or a logged event.** Recommend real UI — it is one button and it closes the "gated" story visually.
