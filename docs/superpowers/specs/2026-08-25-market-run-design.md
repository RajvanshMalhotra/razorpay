# Market Run — Design

**Date:** 2026-08-25
**Plan:** 4 of 5 (schedule days 9–10)
**Branch:** `feat/market-run`
**Status:** design

## The problem

Every mechanism is built and tested — 379 tests, four fix waves, an audited
event log. But **nothing has ever happened**. The real logs hold three actors
and zero settled trades.

That gap is not cosmetic. The house research agent mints intelligence *from
aggregate merchant activity*, and the privacy floor refuses to publish
anything derived from fewer than `K_MIN = 25` distinct merchants. With three
merchants on record, a live run takes the refusal path. The refusal is
correct, and worth showing — but it is not the product.

So Plan 4 has one job: **run the market until the log is worth replaying.**

## What "worth replaying" means

Four properties, in priority order. A run that lacks any of them is a failed
run, however clean the code.

1. **At least 25 distinct merchants settle a real trade.** Below that nothing
   can be minted, so nothing downstream exists.
2. **The house agent finds a pattern that is genuinely there.** Not a pattern
   we wrote into a fixture — one that emerges from what the merchants actually
   did, that a reader can verify against the log.
3. **At least one insight auction clears with real bids and moves points.**
   Second-price, honest valuations, royalties paid to contributors.
4. **The failure path occurs on real data.** A settlement that drifts, an
   accountant that catches it, a freeze that binds, a repair from Razorpay's
   own record. Today this exists only in tests.

## Why a planted trend, and why that is not cheating

The house agent's value is finding a cross-merchant pattern no single merchant
can see. If thirty merchants are given unrelated random needs, there is no
such pattern, and the agent will either report nothing or hallucinate one. The
first is a boring demo; the second is a dishonest one.

So the roster **encodes latent structure**: a cluster of merchants whose
demand for a shared input rises across rounds, and suppliers who serve them.
The rising demand is real — it is in the orders, the negotiations and the
settlements. The agent is not told about it and has no privileged access; it
sees the same aggregate activity as everyone else and has to notice.

The distinction that matters: **we plant the cause, not the conclusion.** A
market where nothing correlates is not more honest than one where something
does — it is just a market with no news in it. Real markets have trends; a
simulation with none is the unrealistic one.

The demo claim stays truthful: *the agent discovered this from the log*, and a
judge can verify that by reading the same log.

## Architecture

Four components, each independently runnable so a failure in one does not cost
the others' work.

### 1. The roster — `scripts/market/roster.py`

Data, not code: 30 merchants, each with an id, a display name, a category, a
short persona, an inventory of assets it can sell, and a schedule of needs it
will try to buy. Personas differ in negotiating style so transcripts do not
read identically.

Structure planted in the data:
- a demand cluster whose need for one input grows over rounds
- suppliers of that input, with varying prices and reliability
- unrelated merchants trading in other categories, so the signal has noise to
  be found in rather than being the only thing present

### 2. The market runner — `scripts/market/run.py`

Rounds. Each round, each merchant with an active need runs the real broker
path: `find_supply` → `assess` → `choose` → `negotiate` → `close`. Settlement
is real Razorpay test-mode.

**Resumable, because it will be interrupted.** This session has already lost
two agents to limits mid-task. The append-only log makes resumption natural:
on start, fold the log, skip what is already done, continue. A run that dies
in round 4 restarts at round 4 and loses nothing.

**Bounded, because it spends real money.** Two ceilings, both checked before
each model call, both leaving a resumable log when hit:
- a token/cost budget for the whole run
- a wall-clock limit

We spent four fix waves making every money action bounded and gated. A runner
that can spend an unbounded amount on model calls would be the same defect in
a different currency.

### 3. The house cycle — `scripts/market/house.py`

After the market has run: `observe` → `mint_from` → `run_auction` →
`settle_purchase` → `pay_royalties`. Brokers value lots in their own words via
`value_insight`. Separate from the runner so intelligence can be re-minted
from an existing log without re-running the market — which is what tuning
means in practice.

### 4. The failure injection — `scripts/market/inject_failure.py`

Makes the drift happen on real data: take a settled trade, arrange for the
local record and Razorpay's record to disagree, then let the accountant find
it. It must use the same reconcile/freeze/repair/resume path as production,
with nothing bypassed.

Deliberately a **separate script**, so the recorded failure is visibly a
detection rather than a scripted scene.

## Cost and time

Roughly 700–900 model calls: ~$20–25 and 1–2 hours wall clock on DeepSeek.
The run is offline, so slowness is free — that was the point of choosing a
recorded video over a live demo.

## What this plan does not do

- No replay UI. That is Plan 5, and it consumes this plan's log.
- No new exchange mechanisms. If the run needs a mechanism that does not
  exist, that is a finding to report, not to build around.
- No tuning of the point economy beyond what the run reveals. Days 9–10
  allow for re-running; the constants stay unless a run shows them wrong.

## Risks

**The trend does not emerge.** The agent may fail to notice the planted
pattern, or describe it uselessly. Mitigation: the house cycle is separately
runnable, so it can be re-run against the same log without re-trading.

**Model output quality.** `llama3.2` already showed format failures. DeepSeek
is stronger, and the auction path now treats an unreadable reply as an absent
bid rather than a zero one — so a bad reply degrades instead of corrupting.

**Razorpay rate limits.** Unknown at 30 merchants. The runner must handle a
rejected call as a settlement failure the accountant can see, never as a crash
that loses the run.

**The run is boring.** The real risk, and not a technical one. A market where
every negotiation succeeds at the asking price has nothing to watch. Success
means visible variety: walked-away negotiations, gate refusals, a trial-sized
first trade with an unknown counterparty that later grows.
