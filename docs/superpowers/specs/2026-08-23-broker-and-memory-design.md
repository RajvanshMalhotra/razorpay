# Design: The Broker and its Memory

**Date:** 2026-08-23
**Plan:** 2 of 5 — days 4–6 of 13
**Depends on:** Plan 1 (exchange core), complete at 105 tests
**Spec it extends:** `2026-08-22-agent-exchange-design.md`

---

## 1. Goal

Give every merchant a representative broker that can actually trade: find supply,
judge a counterparty, negotiate, and decide when to walk away — and remember how it
went well enough that next time is different.

Two things get built, and they are one system:

- **The broker** — an orchestrator with three acting sub-agents and one that only
  remembers.
- **The memory engine** — how the broker knows the present without replaying the past.

**Success criteria**

1. Two brokers negotiate a real trade end to end and settle it through Plan 1's gate.
2. Each sub-agent runs in its own context; none can see another's working memory.
3. A broker's second deal with the same counterparty is visibly informed by the first.
4. A broker walks away from a bad negotiation and states why, in the audit trail.
5. Answering "what is true right now" never replays the whole log.

## 2. Fix first — two defects carried from Plan 1

These are recorded in `learnings_tradeoffs.md` and block correct behaviour here.

**`_record_fill` skips both sides when the bid order is absent from the book.** The
ask then stays open at full quantity, so the re-settlement hazard `ORDER_FILLED`
closed survives on that path. Brokers loop over the open book — they will hit it.

**Both `ORDER_FILLED` events carry the bid's quantity and the buyer as actor.** The
seller's fill is misattributed, and a partial fill of the bid is unrepresentable
because `Match` has no `qty` field. Add one; emit each side's own quantity and actor.

## 3. The memory engine

Three layers over one history. Layers 1 and 2 exist in part; layer 3 is new.

```
                    IMMUTABLE EVENT LOG            (built)
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
      STATE PROJECTION                CONTEXT TREE      (new)
      "what is true now"            "how we got here"
              │                             │
              │                    ┌────────┴────────┐
              │                 deltas          checkpoints
              │                             │
              └─────────────┬───────────────┘
                            ▼
                    CONTEXT RETRIEVAL
                            ▼
                      AGENT INPUT
```

### 3.1 State projection — incremental, with an offset

Today `fold()` rebuilds everything from event 1 on every read, three times per trade.
Add an `event_offset` to the projection: the log line through which it is correct.

```
State:
    points_balance    { merchant_a: 4200, merchant_b: 900 }
    spend_against_cap { merchant_a: 1800 }
    units_remaining   { ord_7: 300 }
    event_offset      8412
```

Reading: if the log still ends at 8412, answer from memory. If it ends at 8417, apply
events 8413–8417 and move the offset. Never return to line 1.

**It holds exactly three things**, because these are what an agent needs to trade and
none of them may ever be stale: **points balance**, **spend against cap**, and **units
remaining per order**. Anything that can tolerate being a few minutes old does not
belong here.

**The correctness obligation.** The answer now exists in two places, and a silently
wrong balance is a wrong money decision. The accountant rebuilds from the log on a
schedule, compares, and freezes on mismatch — the same mechanism it already uses
against Razorpay. Fast reads are only acceptable *because* something proves them.

`fold()` stays exactly as it is and remains the authority. The incremental path is an
optimisation over it, never a replacement.

### 3.2 Context tree — how the agent got here

Each execution is a node:

```
ContextNode:
    id, parent, children
    context_delta          # what this execution changed
    state_version          # which projection it saw
    event_offset           # where the log was
```

Context is **semantic, not a transcript**:

```
ContextState:
    objective
    constraints
    decisions
    facts
    unresolved_questions
    artifacts
```

A node stores the previous state **plus a delta**, never a copy. `State(t+1) =
State(t) + Δ(t+1)`. Copying whole contexts down a chain is quadratic in storage;
deltas are linear.

**Deltas are additive-only on `facts` and `decisions`.** The single field a delta may
remove from is `unresolved_questions`, where removal *is* the semantics — a question
got answered. Nothing else ever shrinks. A checkpoint that can drop a fact quietly
rewrites history, and the agent has no way to know something is missing.

**Checkpoints land on episode boundaries** — a completed trade — not a fixed interval.
Semantically meaningful, self-tuning, and it makes a checkpoint mean *everything the
broker knew when that deal closed*, which is exactly what the Subconscious consolidates.
Reconstruction walks back to the nearest checkpoint and replays deltas forward.

**Retrieval is leaf-first, ancestor-aware, relevance-driven.** Start at the current
leaf — it carries `state_version` and `event_offset`, so current state is immediate.
Climb toward ancestors only when the question needs history. Never walk the whole tree.
The leaf says *what just happened*; ancestors say *why*.

### 3.3 What is deliberately not built

**No hand-built B+ tree.** SQLite's indexes are B-trees. `timestamp → execution`,
`execution_id → checkpoint`, `version → state` are three `CREATE INDEX` statements.

**No context merging, because nothing merges.** See §4.2.

## 4. The broker

### 4.1 Four parts

| Part | Acts | Context holds | Promotes upward |
|---|---|---|---|
| **Trader** | yes | Inventory, needs, open orders, counterparty terms | *"Short on X; merchant_41 quotes best"* |
| **Scout** | yes | Trend feeds, captured signals, bid history | *"Vitamin-C demand rising; lot worth ~1,850"* |
| **Diplomat** | yes | Who we have dealt with, how it went, what they care about | *"41 reliable, warm to 09; try the unknown small"* |
| **Subconscious** | no | Every episode this merchant has lived | Injects recall before each action |

Each acting sub-agent gets its own context window and its own subtree. None can read
another's working memory — that isolation is what stops a broker quoting what it paid
supplier A while negotiating with supplier B.

### 4.2 Sub-agents narrow; they never merge

Each emits a **structured summary** that becomes a *fact* in the orchestrator's delta.

```
Trader   ─┐
Scout    ─┼──►  one-way narrowing  ──►  orchestrator Δ.facts
Diplomat ─┘         (not a merge)
```

Narrowing is safe in a way merging is not: you are choosing what to promote, not
reconciling two versions of the same thing. Executions branch on the way down and
never rejoin, so there is no diamond and no reconciliation problem to solve.

### 4.3 The Subconscious

Two operations, no actions.

**Consolidate** — after each completed episode, distil it into a durable lesson keyed
by counterparty, category and outcome. Episodic becomes semantic. Runs at the episode
checkpoint, so consolidation and checkpointing are the same moment.

**Recall** — before a sub-agent acts, retrieve the lessons relevant to *that* action
and inject them into its context.

```
Dealt with merchant_41 three times. They push hard on delivery dates.
Paid nine days late once. Said yes fast to a volume discount.
Don't open on price.
```

Lessons distinguish **behavioural** observations (*haggles hard* — neutral, useful)
from **reliability** observations (*didn't deliver* — costly). Only the latter moves
`reliability_score`, and a single data point widens uncertainty rather than collapsing
it.

## 5. Negotiation

### 5.1 A round is one message

Settled here because Plan 1's spec left it ambiguous, and the answer doubles or halves
both cost and screen time. One message per round; each side still gets multiple turns.

### 5.2 Walking away is a judgment, not a counter

A hard round cap makes brokers look like scripts and yields no signal — a counter tells
you *that* it stopped, never *why*. `MAX_NEGOTIATION_ROUNDS = 4` in `config.py` is an
arbitrary constant nothing consumes; it is replaced by four layers.

| Layer | Stops it because | Appears as |
|---|---|---|
| **Reasoning** | Gap not worth it; better seller available; Subconscious says hold | The product. On screen. |
| **Progress** | The *gap between the two sides* has not moved in two exchanges | *"Neither side moved. Ending."* |
| **Backstop** | Token budget for this negotiation exhausted | Should never fire. A bug if it does. |
| **Run** | Wall clock on the whole market run | Never visible inside a trade. |

Measure movement of the **gap**, not of each offer — otherwise oscillation
(₹19 → ₹20 → ₹19) reads as motion with no progress.

The backstop is a **token budget**, not a round count: tokens are the real cost being
bounded, and the number lands in the log so runs stay reproducible. A wall-clock cap on
a single negotiation is rejected — *"stopped after 60 seconds"* is not a reason a broker
would give, and it makes behaviour depend on API latency rather than on agents.

### 5.3 What the system prompt weighs

Backing off is a judgment call, so give it what a real broker weighs: is the gap
closing; is there a better candidate already in hand; is the remaining gap worth the
spend; and what does the Subconscious say about this counterparty's pattern.

`NEGOTIATION_ENDED` is logged with the agent's stated reason on the same
`correlation_id` — both the signal and a good few seconds of video.

## 6. New events

`CONTEXT_NODE_CREATED`, `CONTEXT_CHECKPOINT`, `LESSON_CONSOLIDATED`,
`NEGOTIATION_OPENED`, `NEGOTIATION_ROUND`, `NEGOTIATION_ENDED`, `RECALL_INJECTED`.

Every one carries the trade's `correlation_id`, so a broker's reasoning replays
alongside its trades rather than in a separate stream.

## 7. Phasing across three days

Ordered so that a working broker exists even if the back half slips.

| Day | Build |
|---|---|
| 4 | Carried defects; `Match.qty`; broker skeleton — orchestrator plus three isolated sub-agent contexts; a single broker posts a real descriptive bid |
| 5 | Negotiation between two brokers with reasoning-driven backoff and the progress detector; relationship graph |
| 6 | Context tree with deltas and episode checkpoints; Subconscious consolidate and recall; incremental state projection |

**If day 6 slips, the incremental state projection is dropped first** — it is a
performance optimisation worth ~0.2% of runtime today, and `fold()` already works. The
Subconscious is not droppable; it is the differentiator.

## 8. Risks

| Risk | Mitigation |
|---|---|
| LLM cost dominates everything — ~15 calls per trade | Token budget per negotiation; small models for sub-agents; cache signal fetches |
| Brokers converge to identical behaviour, making a dull run | Day 9–10 tuning; anti-incumbency already forces exploration |
| Context tree is the most novel piece and could eat the whole plan | Phased last; broker works without it |
| Semantic compression silently loses a fact | Additive-only delta rule; checkpoints rebuildable from the log |
| Incremental state drifts from the log | Accountant reconciles and freezes; `fold()` stays authoritative |

## 9. Open questions

1. **Which model per sub-agent?** Recommend a small fast model for Trader/Scout/Diplomat
   and a stronger one for the orchestrator and the Subconscious, where the judgment lives.
2. **How many brokers in the first run?** Recommend starting at 3 to keep negotiation
   traces readable, then raising to 6–8 for the recorded run.
3. **Does the Subconscious consolidate with an LLM call or a rule?** Recommend LLM —
   the whole value is in distilling *"they haggle but always fold on delivery"*, which
   rules cannot express.
