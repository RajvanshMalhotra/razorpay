# Design: The Intelligence Economy

**Date:** 2026-08-23
**Plan:** 3 of 5 — days 7–8 of 13
**Depends on:** Plan 1 (exchange core) and Plan 2 (broker and memory), both complete
**Extends:** `2026-08-22-agent-exchange-design.md`, `2026-08-23-broker-and-memory-design.md`

---

## 1. Goal

Razorpay sits on every transaction across every merchant, so it can see things no single
business can. This plan turns that into a product: a house agent that mines cross-merchant
activity into market intelligence, publishes free headlines to a victory feed, and auctions
the details to broker agents for points.

And it changes one thing about how brokers decide, because the alternative is watching
hand-set weights multiply.

**Success criteria**

1. The house agent mints an insight lot, publishes a headline, and runs a sealed-bid
   auction that clears second-price in points.
2. A broker's Scout *judges* what a lot is worth to its merchant and says why — no scoring
   formula.
3. Points are earned only by the accountant, from settled activity, and a contributor earns
   when their own win is bought.
4. The privacy floor refuses to publish a lot derived from too few merchants, visibly.
5. The accountant detects a settlement that drifted from Razorpay's own record, freezes the
   agent, repairs, and resumes — the track's "one failure handled gracefully".
6. No hand-set weight decides which counterparty or which lot an agent picks.

## 2. The governing principle: agents choose, the gate bounds

Plan 2 ended with a matcher that ranks candidates by score, sorts, and returns the winner.
The broker then narrates a decision arithmetic already made. That is a sorting function with
a personality, and every tie it breaks needs a weight somebody guessed.

**From this plan on, the split is:**

| Decided by an agent, with reasons | Decided by a hard number |
|---|---|
| Which of several sellers to trade with | How much may be spent at all |
| Whether a stranger is worth trying | How much may be risked on a stranger |
| What an insight is worth to *this* merchant | Whether a lot may be published at all |
| When a negotiation is not worth continuing | The token budget behind that judgment |

The left column is judgment and belongs in the audit trail as prose. The right column is
what makes "bounded and gated" true, and no agent may move it. An agent that sets its own
spending cap is not bounded, and that sentence is the whole bar.

**What this deletes.** `COUNTERPARTY_WEIGHT` and the multiplicative standing nudge go away
entirely. `find_candidates` returns a shortlist ranked by *retrieval relevance only*; the
Diplomat's read and the counterparty's history become **facts in the choosing agent's
context**, not a multiplier on a score. One fewer number nobody could justify.

**What it costs.** One model call per trade to choose. Cheap beside a negotiation, and it
produces the labels a learned ranker would need later — see §8.

**Suboptimal is the point.** A broker that always takes the cheapest offer is arithmetic.
One that pays ₹40 more because the seller has never missed a delivery and the launch is
Friday is a broker. Two runs of the market will differ, which makes a replay worth watching
twice.

## 3. The house agent

Not a merchant. It mints, publishes and auctions; it never buys.

### 3.1 Insight lots

```
InsightLot.spec:
    headline          public, free — the victory feed entry
    playbook          private, auctioned — channel, creative, spend, audience, timing
    contributor_ids   merchants whose activity it derives from
    k                 aggregation count, must be >= K_MIN (25)
    category
```

`Asset.kind == INSIGHT` implies `currency == CREDITS` — already enforced in Plan 1.

### 3.2 The privacy floor is a gate, not a promise

Before any lot is listed, a check runs and is **logged like a policy decision**:

- `k >= K_MIN` — no lot derived from fewer than 25 merchants
- no output attributable to a single merchant
- contributors opted in at plan level

A failure emits a `DENY`-shaped record and the lot is never listed. This must be visible in
the audit trail, because "you're selling my campaign to my competitor" is the first question
a Razorpay judge asks, and the answer needs to be mechanical rather than reassuring.

### 3.3 The victory feed

The free half. A running list of headlines — *"a D2C skincare brand beat category
conversion 3.2x last week"* — with no playbook attached. The headline creates the demand;
the auction prices it.

## 4. Auctions

Sealed-bid, second-price. All bids submitted blind; highest wins and pays the second-highest.

```python
bids.sort(key=lambda b: b.amount, reverse=True)
winner, price = bids[0], bids[1].amount
```

One bidder means no second price — the lot does not clear. That is correct: a market of one
has no price.

**The Scout judges the bid; nothing computes it.** Given the headline, the category, its
merchant's current position and what the Subconscious recalls about past lots, the Scout
returns an amount and a sentence of reasoning. Both are journalled. Under second-price,
bidding true value is the dominant strategy, so the reasoning that lands in the log is about
worth — *"we spend ₹40k a month in exactly this category"* — rather than about guessing
rivals.

**The bid is still bounded.** `DEFAULT_CREDIT_LIMITS` caps what any agent may bid, and the
gate refuses an overbid exactly as it refuses an overspend. Judgment picks the number; the
cap decides whether it is allowed.

## 5. Points

**Earned only by the accountant**, from settled INR activity:

- margin captured against ask (primary)
- fill rate
- counterparty reliability delivered
- volume (minor term)
- **contributor royalty** — when a lot derived from your activity is bought, you earn a share

Volume-weighting is rejected: it makes the largest merchant win by round three and kills both
the economy and the demo. A small merchant that negotiates sharply must out-earn a large one
that overpays.

**Spent** on insight auctions. **Damped** by slow decay and per-tier caps so the flywheel
cannot run away. **Convertible** to Razorpay fee rebates — which is what makes them income
rather than a closed loop, and costs Razorpay margin instead of cash.

## 6. The accountant

Exchange-level, not per-merchant: reconciliation needs both sides of every trade, and point
conservation is a global invariant. Its per-merchant statement is projected into each
broker's context.

Runs on a periodic pass:

1. **Reconcile** — fetch Razorpay test-mode payment state, compare against local
   `Settlement` records. Detects the dropped webhook: captured upstream, still `PENDING`
   locally, and the inverse.
2. **Assert invariants** — points conserved and minted only here; no `Settlement` without a
   preceding `ALLOW`; no orphaned matches (**join on `POLICY_DECIDED`, not on presence** —
   `MATCH_PROPOSED` precedes the gate by design, so a denied match is in the log legitimately);
   asset kind ↔ currency consistency; and **`Exchange._state_cache` agrees with a full
   `fold`** — the incremental projection's correctness rests on this check existing.
3. **Mint points** — the earnings formula lives here alone.
4. **Halt on drift** — set `status = FROZEN`, emit an event, block trading until repaired.

**This is the failure demo.** A capture succeeds at Razorpay but the local record stays
`PENDING`; the accountant catches the mismatch, freezes the agent mid-trade, repairs from the
log, and resumes. Deliberately not a declined card — every team will show one of those.

**It also closes the reliability half of the memory loop.** Plan 2 consolidates behavioural
lessons at settlement because delivery is unknown then. The accountant's reconciliation *is*
the delivery signal: a settlement that completes cleanly is evidence of reliability, one that
drifts is evidence against. `RelationshipGraph.apply_lesson` finally gets a production caller
that can move standing.

## 7. Per-role model tiers

The expensive model is the rarest call, which is the opposite of the intuition:

| Role | Tier | Calls per trade |
|---|---|---|
| Subconscious — consolidating a whole episode | strong | 1 |
| Orchestrator — choosing from the shortlist | strong | 1 |
| Scout — valuing a lot | strong | ~1 per auction |
| Trader / Diplomat | fast | ~6–10 |
| Negotiation rounds | fast | ~4–8 |

`Broker` takes a provider **per tier** rather than one shared provider. Local development
points both tiers at the same Ollama model; production uses `deepseek-v4-pro` for the strong
tier and `deepseek-v4-flash` for the fast one. Cost is not the constraint — mixed tiering
lands below the ~$11 per 500 trades that all-pro would cost — so the choice is purely about
where judgment lives.

## 8. Deliberately not built

**A learned ranker.** An MoE or any scoring network needs labels, and there are none: the
market has not run. Training on synthetic data generated by the heuristics being replaced
would launder hand-set weights into parameters nobody can read, and produce `0.734` where the
audit trail wants a sentence. **The correct sequence is: agents choose → the market runs →
outcomes exist → then a small learned model earns its place as a fast pre-filter feeding the
agent.** This plan generates those labels.

**Windowed rolling spend.** Still cumulative — strictly tighter, so safe. It will begin
refusing legitimate trades over a long run; revisit in Plan 4 when the run length is known.

## 9. Carried in from Plan 2

Fix at the start, before building on them:

1. **Only the Trader's summary is promoted** to the orchestrator. The Diplomat's and Scout's
   are discarded, and `find_supply` reassigns `root_id` while the sub-agents keep their
   original parent — so promoted facts land on a chain the sub-agents cannot see, growing
   without a checkpoint. §2's choosing agent needs all three.
2. **`agreed_price` defaults to `None`**, so nothing structurally forces a negotiated price
   through to settlement. A caller that forgets silently reintroduces a Critical.

## 10. Phasing across two days

| Day | Build |
|---|---|
| 7 | Carried fixes; agents choose from a shortlist; per-tier providers; house agent, insight lots, privacy floor, victory feed |
| 8 | Sealed-bid second-price auction with Scout valuation; points ledger and earnings; the accountant with reconciliation, invariants and freeze-repair-resume |

**If day 8 slips, the points *earning formula* is the first thing cut** — award a flat stipend
so auctions still run. **The accountant is not droppable**: it is the failure demo and the
reliability signal, and without it the incremental projection has nothing proving it.

## 11. Risks

| Risk | Mitigation |
|---|---|
| Agents choosing adds a model call per trade and per auction | Strong tier only where judgment lives; measure on a 20-trade run before the long one |
| Small local models judge badly — Plan 2 saw a broker walk away from a plainly good deal | Test the strong tier against DeepSeek early, not on day 11 |
| The auction never clears with few bidders | Second-price needs two bids; seed enough brokers, and treat a no-clear as a legitimate logged outcome |
| Removing the counterparty weight changes matching behaviour | The shortlist is retrieval-ranked and the agent sees the same facts the weight encoded; regression-test that a stranger still appears |
| Accountant freezing an agent mid-run stalls the market | Freeze is per-actor, not global; repair path must be exercised in a test, not just designed |

## 12. Open questions

1. **How large a shortlist?** Recommend 3 — enough for a real choice, small enough that the
   reasoning stays readable on camera.
2. **Does the house agent mine real settled activity, or a seeded corpus?** Recommend real,
   read from the event log — it makes the "only Razorpay could build this" claim true rather
   than asserted.
3. **What does a contributor royalty pay — a fixed share or a share of clearing price?**
   Recommend clearing price, so a valuable win earns more.
