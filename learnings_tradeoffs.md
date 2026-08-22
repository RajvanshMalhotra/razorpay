# Learnings & Trade-offs

A running record of the decisions behind this project — what we chose, what we
rejected, and what we found out the hard way. The process ledger in
`.superpowers/sdd/` is bookkeeping; this file is the part worth keeping.

Started 2026-08-22. Deadline 2026-09-04.

---

## 1. Design trade-offs, decided before any code

### 1.1 One agent with three faces, not three products

The original idea contained four independent products: an agent-readable storefront,
an agent-to-agent transaction rail, an always-on growth agent, and a memory engine.
In 13 days solo that buys one good product or three broken ones.

**Chosen:** one representative broker per merchant, with three acting sub-agents
(Trader, Scout, Diplomat) plus a passive Subconscious, all sharing one memory spine.
The exchange is the build; growth feeds it demand and the storefront feeds it revenue.

**Why it works:** the memory/context engine stops being a nice-to-have and becomes the
load-bearing piece all three share.

### 1.2 Autonomous outreach — cut deliberately

An agent that joins Discord/Telegram/WhatsApp servers and DMs founders on Reddit or
Instagram violates the terms of essentially all of those platforms, and reads to judges
as a spambot — which cuts directly against the track's "bounded and gated" bar.

**Chosen:** growth signal from public read-only sources; anything outbound is
human-approved. Same ambition, and it *demonstrates* the bar instead of fighting it.

### 1.3 Two currencies on two rails

Goods and services settle in real Razorpay test-mode money. Intelligence is bought with
internal points.

**Why:** the fiercely competitive bidding — the part most likely to produce a runaway
agent — happens entirely in play money. The safety property is structural, not bolted
on afterwards.

### 1.4 Points reward broker skill, not volume

Rejected volume-weighting: it makes the largest merchant win by round three, starves
small merchants of intelligence, and leaves nothing interesting on screen. Points come
from margin captured against ask, fill rate and counterparty reliability, with volume as
a minor term. A small merchant that negotiates sharply out-earns a big one that overpays.

### 1.5 Anti-incumbency: explore, bounded

**The flaw, spotted in design review:** if agents prefer merchants they have history
with, those merchants win all the business and accrue more history. New merchants never
get a first deal, so they never get history. The market ossifies into cliques and every
new Razorpay merchant is frozen out.

**The fix, three rules:**
1. Unknown counterparties are scored *optimistically*, so the Diplomat actively wants to
   try them.
2. First deals with a stranger are capped small, enforced by the policy gate. The cap
   rises with track record.
3. One bad deal barely moves the score, and "haggled hard" is distinguished from
   "didn't deliver".

Structural rule: **the Diplomat advises, it never vetoes.** Reputation may reorder
candidates; it may never remove one. Risk is bounded by trade size instead.

This turned out to have real teeth in the code — see §3.4.

### 1.6 Offline market, replay visualisation

A recorded video means the exchange is allowed to be slow. Live-demoing multi-agent LLM
negotiation is 30 seconds of dead air per round, and one bad sample kills the take.

**Chosen:** run the real agents offline for hours, write every event to a log, build a
replay UI, shoot the video against the replay. Real emergent behaviour at zero demo risk,
and the run can be repeated until one is genuinely interesting.

**The bonus that justified the whole architecture:** the event log we replay *is* the
audit trail the track asks for. One artifact, two jobs.

### 1.7 Second-price auctions — kept, but deliberately de-emphasised

Sealed bids, highest wins, pays the runner-up's price. It makes honest valuation the
dominant strategy, so agents reason about worth instead of guessing rivals, which keeps
their on-screen reasoning legible.

**Honest assessment:** this is one line of code and was oversold when first presented.
Worth keeping because it is free; not a pillar, and it does not need to appear in the
video.

---

## 2. Method trade-offs

### 2.1 Plan-as-reference-implementation

The implementation plan carries complete code for every task, so most tasks are
transcription plus verification rather than design. This let Tasks 2–7 run on the
cheapest model — the judgment was spent once, when the plan was written.

**The cost, flagged by a reviewer and worth stating plainly:** those tasks carry
near-zero independent-judgment risk, which also means their tests validate
*code-matches-plan*, not *code-matches-reality*. Two genuine bugs (§3.3, §3.4) were in
the plan's own reference code and passed every test written for them. Transcription
fidelity is not correctness.

**Mitigation adopted:** exercise the real production path by hand at least once per
component, outside the test suite. That is how both bugs were found.

### 2.2 Verify claims independently, don't trust reports

Every task report was checked against the actual system rather than accepted:
append-only triggers were fired at the database level, credit conservation was tested
over 400 randomised transfers, the policy gate was swept across 13 boundary cases, and
the anti-incumbency property was measured at four standing values. Two of these
verifications went beyond what the plan's tests cover, and one of them (§3.3) found a bug.

---

## 3. What we found out the hard way

### 3.1 A Razorpay payment cannot be created server-side

**Probed, not assumed.** Against live test mode:

| Operation | Result |
|---|---|
| `order.create` | works — `order_TSvlfCMLyvzRoF` |
| `order.payments(order_id)` | `{"count": 0, "items": []}` — no payment, ever |
| `payment_link.create` | works — `plink_TSvlgNs8fC4u15`, returns a `short_url` |

**Consequence:** `settle()` polls for a captured payment and will therefore leave every
INR settlement `PENDING` in a fully automated run. The simulation cannot produce a
captured payment on its own. This directly affects the video's "real payment ID on
screen" moment.

**Ruling:** keep the polling loop — `PENDING` is a real state the accountant reconciles,
not a bug. Additionally create a Payment Link per settlement and record its `short_url`
in the event payload, so a human or browser automation can complete payment with the test
card and produce a genuine captured payment id. Unpaid trades stay `PENDING` and become
the accountant's reconciliation workload, which is authentic rather than a workaround.

**Sub-ruling:** a failed payment link must **not** fail the settlement. The order still
exists and is payable by other means; converting a lost convenience link into a failed
settlement would discard a real Razorpay order.

### 3.2 `torch` would not install, and did not need to

`sentence-transformers` pulls torch (~2.5GB) onto a disk with ~5GB free. The install
failed.

**Measured rather than guessed:** `model2vec` plus every transitive dependency is **15MB
and needs no torch**, and provides real distilled static embeddings — so the paraphrase
capability survives.

**Rejected:** a hashing/bag-of-words embedder. It would have installed instantly and
silently destroyed paraphrase matching, which is the entire reason for having a dense
path beside BM25.

**Caught while writing the swap:** the cosine function is a bare dot product assuming
unit vectors — a property `sentence-transformers` guaranteed via
`normalize_embeddings=True` and model2vec does not provide by default. Without explicit
L2 normalisation, longer listings would have outranked more relevant ones, and no test
would have noticed.

### 3.3 An abstaining keyword matcher was outvoting the embeddings

**The bug.** BM25 scores a listing by shared words. For "something for my skin" it scored
every listing `0.0` — including the vitamin C serum, because `skin` and `skincare` are
different tokens. Zero across the board is BM25 correctly saying *"I have no opinion."*

The code then sorted by score and passed the list on. With every score tied at zero,
Python's stable sort left them in insertion order — so the shrug came out looking exactly
like a confident ranking. Reciprocal rank fusion only reads *positions*, never the scores
behind them, so it counted the shrug as a vote and it beat the embeddings, which actually
knew the answer.

```
query 'something for my skin'   before: ['ast_boxes', 'ast_serum']   ← boxes beat serum
                                 after: ['ast_serum', 'ast_bubble']
query 'eco friendly packaging'  before: ['ast_boxes', 'ast_mailers']
                                 after: ['ast_mailers', 'ast_boxes']
```

**Fix:** drop zero-scoring documents before building the ranking, so a retriever with
nothing to say contributes an empty list rather than a fake ranking.

**Why it mattered:** descriptive bids are the core feature — both the Trader hunting
supply and the human storefront route through this one function. Unfixed, any request
phrased in words absent from the catalogue would have ranked by insertion order while
looking entirely plausible. That is the failure mode that survives to demo day.

**Post-fix, the paraphrase capability demonstrably works:** *"protect fragile items in
transit"* now returns bubble wrap, sharing no word with `"bubble wrap rolls plastic
protective"`.

**A related concern, investigated and closed:** BM25's IDF can go negative for terms
appearing in most of a corpus, which would make a `> 0` filter drop genuine matches.
Tested rather than reasoned about — `rank_bm25` floors negative IDF via `epsilon=0.25`,
so a term present in *every* document still scores 0.127. Matching documents always score
above zero; non-matching score exactly zero. The filter is precisely correct.

### 3.4 Competing offers on one listing were silently discarded

The matcher grouped eligible offers into a dictionary keyed by *which asset* they point
at. Two offers on the same asset meant the dictionary kept only the last — chosen purely
by list position.

**Why it is load-bearing, not theoretical:** an asset belongs to one merchant, so the
realistic case is one merchant posting volume tiers on its own listing (500 @ 19.40,
1000 @ 18.00) or partial fills. That is ordinary order-book depth, and the Trader agents
in the next plan generate it immediately. The market would have quietly ignored its own
depth, surfacing much later as "the market behaves oddly."

**Fix:** group offers per asset instead of overwriting. **And** add a price tie-breaker —
because two offers on one asset score identically on relevance, so without one the winner
would fall to reputation, and where that is also equal, back to insertion order. A buyer
handed the pricier of two identical offers is a worse bug than the one being fixed.

### 3.5 Library code that reads the filesystem cannot be tested

`Config.from_env()` called `load_dotenv()` internally. The moment a real `.env` existed,
the test asserting "missing key raises" failed — `load_dotenv` helpfully refilled the
value from disk.

**Ruling:** loading `.env` is an application-entry-point concern, not library behaviour.
It moved to the scripts that are its only real callers.

**Worth noting:** the first implementer only got its tests passing by moving `.env` out
of the way, which meant the suite was never proven in the configuration it actually runs
in. Tests that pass only in a hand-arranged environment are not passing.

---

## 4. Bug log

Every defect found during the build, with how it was caught. The pattern worth
noticing: **three of the four were in the implementation plan's own reference code**,
and every one of those passed the tests written for it — because the tests came from
the same document as the code. None were found by the test suite. All were found by
running the real thing, or by review.

| # | Bug | Where | Found by | Status |
|---|---|---|---|---|
| 1 | Library code read the filesystem, so tests depended on environment | `config.py` | Real `.env` appearing mid-run | Fixed |
| 2 | Abstaining keyword search outvoted the embeddings | `retrieval.py` | Exercising the real embedder by hand | Fixed |
| 3 | Competing offers on one listing silently discarded | `matching.py` | Code review | Fixed |
| 4 | Payment-link failure recorded no reason | `rails/inr.py` | Code review | Fixing |

---

### BUG-1 — `load_dotenv()` inside `Config.from_env()`

**Symptom.** The test asserting "a missing Razorpay key raises `ValueError`" passed on a
clean machine and failed the moment a real `.env` existed.

**Root cause.** `from_env()` called `load_dotenv()` internally. The test deleted the
environment variable; `load_dotenv()` then helpfully refilled it from disk, so no error
was raised. The function's behaviour depended on a file that may or may not be there.

**Fix.** Loading `.env` moved out of the library and into the two scripts that are its
only real callers. `from_env()` now reads the environment and nothing else.

**How it was found.** Not by the suite — by adding real credentials and watching a
previously-green test go red.

**The wider lesson.** The first implementer got its tests passing by moving `.env` out
of the way. That is worth naming: a suite that only passes in a hand-arranged
environment is not passing. The fix was to change the code, not the environment.

---

### BUG-2 — BM25's abstention was outvoting the embeddings

**Symptom.** Searching *"something for my skin"* returned a corrugated-boxes listing
above a vitamin C serum.

**Root cause.** BM25 scores by shared words. `"skin"` and `"skincare"` are different
tokens, so every listing scored `0.0` — BM25 correctly saying *"I have no opinion."*
The code then sorted by score and passed the result on. With every score tied at zero,
Python's stable sort left the listings in insertion order, so the shrug came out looking
exactly like a confident ranking. Reciprocal rank fusion reads only *positions*, never
the scores behind them, so it counted that shrug as a firm vote — and it beat the
embeddings, which actually knew the answer.

**Fix.** Drop zero-scoring documents before building the ranking. A retriever with
nothing to say now contributes an empty list instead of a fake ranking.

```
'something for my skin'            before: ['ast_boxes','ast_serum']   after: ['ast_serum','ast_bubble']
'eco friendly packaging'           before: ['ast_boxes','ast_mailers'] after: ['ast_mailers','ast_boxes']
'protect fragile items in transit'                                     after: ['ast_bubble', ...]
```

That last query shares no word at all with *"bubble wrap rolls plastic protective"* —
it resolves purely on meaning. That is the paraphrase capability descriptive bids exist
for, and it only works with the fix in.

**How it was found.** By calling the production embedder by hand. Every test injects a
fake embedder, so the entire suite was blind to it.

**Why it mattered.** Descriptive bids are the core feature — the Trader hunting supply
and the human storefront both route through this one function. Unfixed, any request
phrased in words absent from the catalogue would rank by insertion order while looking
entirely plausible. That is the failure mode that survives to demo day.

**A related scare, investigated and dismissed.** BM25's IDF can go negative for terms
appearing in most of a corpus, which would make a `> 0` filter drop genuine matches.
Tested rather than argued about: `rank_bm25` floors negative IDF at `epsilon = 0.25`, so
a term present in *every* document still scores 0.127. Matching documents always score
above zero, non-matching exactly zero. The filter is precisely correct.

---

### BUG-3 — competing offers on one listing were silently discarded

**Symptom.** None visible. That is what made it dangerous.

**Root cause.** The matcher collected eligible offers into a dictionary keyed by *which
asset* they point at. Two offers on the same asset meant the dictionary kept only the
last — selected purely by list position, before relevance or reputation was ever
considered.

**Fix.** Group offers per asset instead of overwriting. **And** add a price tie-breaker:
two offers on one asset necessarily share a relevance score, so without one the winner
would fall to reputation, and where that was equal too, back to insertion order. A buyer
handed the pricier of two identical offers is a worse bug than the original.

**How it was found.** Code review, after being explicitly asked to judge what happens
when two offers reference the same asset.

**Why it mattered.** An asset belongs to one merchant, so the realistic trigger is one
merchant posting volume tiers on its own listing — 500 @ ₹19.40, 1000 @ ₹18.00 — or
partial fills. That is ordinary order-book depth, and the broker agents in the next plan
generate it immediately. The market would have quietly ignored its own depth and
surfaced much later as *"the market behaves oddly and I don't know why."*

**Verified after fixing:** three tiers posted dearest-first (2100, 1940, 1800) all
survive and return cheapest-first.

---

### BUG-4 — a swallowed payment-link failure left no reason

**Symptom.** A settlement with `payment_link_url: None` and nothing anywhere explaining
why.

**Root cause.** The `except` around payment-link creation discarded the exception. An
operator cannot tell "the link was never attempted" from "the link service returned a
500" — unlike the order-creation failure path, which records a `reason`.

**Fix.** Record `payment_link_error` in the event payload alongside the null URL.

**How it was found.** Code review.

**Why it mattered enough to fix rather than defer.** The audit trail is this project's
actual deliverable. A trail that records an absence without recording its cause is a
weaker artifact. Concretely, the accountant in the next plan reconciles against these
payloads, and a human chasing an unpayable settlement needs to know whether to retry the
link or investigate the account.

---

### BUG-5 to BUG-12 — what the final whole-branch review found

Ten per-task reviews all passed clean. The final review, reading the whole branch at
once, found six more real defects. **Every one of them lived in a seam between modules
that no single task's review could see.** That is the argument for the final pass.

| # | Bug | Severity | Status |
|---|---|---|---|
| 5 | The match never entered the audit trail | Important | Fixed |
| 6 | Matching asserted a relevance it never established | Important | Fixed |
| 7 | The two rails disagreed about failure | Important | Fixed |
| 8 | An invariant test asserted less than its name | Important | Fixed |
| 9 | Orders were never depleted after a trade | Important | Fixed |
| 10 | The rolling-spend cap was unenforceable | Important | Fixed |
| 11 | Dense ranking still decided by insertion order | Important | Fixed |
| 12 | Latent `KeyError` on a FAILED-without-INITIATED log | Important | Fixed |

**BUG-5 — the match never entered the audit trail.** `MATCH_PROPOSED` was declared in the
event vocabulary and never emitted. `PolicyDecision.action_ref` pointed at a match id that
appeared in no event, and `Match.rationale` — the one artifact explaining *why* two parties
were paired — was computed and discarded. A `correlation_id` reconstructed *"actor X was
allowed to spend N and did"* but not *against whose offer, at what price, on what
reasoning*. That is the difference between an audit trail and a payment log, and it is the
exact claim this project is judged on. Fixed by appending the match before the gate and
chaining match → decision → settlement by `causation_id`.

**BUG-6 — matching asserted a relevance it never established.** No score floor, and the
dense ranking was never truncated, so any feasible ask matched any bid. A bid for
*"corrugated kraft boxes for shipping"* matched a vitamin C serum and printed
`"ast_serum matched 'corrugated kraft boxes for shipping'"`. That word is false, and it is
what the audit trail printed. Fixed by making the rationale report the score rather than
assert a match, plus a tunable floor. **A non-zero default was deliberately refused:** RRF
scores are rank-derived and not comparable across corpora, so picking a number without
real listing data would be guessing, and a wrong floor silently rejects valid trades.

**BUG-7 — the rails disagreed about failure.** The credits rail raised before writing
anything; the INR rail logged a failure and returned. Both implement the same protocol. So
a short balance propagated out having already written `POLICY_DECIDED: ALLOW` — a permanent
gate-said-yes with no settlement outcome. *"Every settlement had an ALLOW"* still held;
*"every ALLOW resolved"* did not. The second is the one a reconciler needs.

**BUG-8 — a test asserted less than its name.** `test_every_settlement_is_preceded_by_an_allow_decision`
took the most recent decision *anywhere* in the log rather than correlating it to the
settlement. With interleaved matches — DENY on A, ALLOW on B, settlement for A — it passed
while the invariant was broken. This is the clearest instance of the pattern in §2.1: a
test that validates the plan rather than reality.

**BUG-9 — orders were never depleted.** No `ORDER_FILLED` event existed, so after a settled
trade both orders stayed open at full quantity. A broker looping over the open book would
have re-matched and re-settled the same inventory indefinitely, creating a real Razorpay
order each time.

**BUG-10 — the rolling-spend cap was dead code.** Caller-supplied and every caller passed
zero, while the gate reported having evaluated it. One of five spec-required limits was
unenforceable. Now derived from the log. Cumulative rather than time-windowed — stricter,
and documented as such.

**BUG-11 — the insertion-order bug had a twin.** BUG-2 was fixed on the keyword side. The
reviewer proved the identical defect was still live on the embedding side: tied dense
scores fell back to a stable sort, so listing order silently decided ranking. **The lesson
is about the fix, not the bug:** the original patch treated one symptom instead of the
class. The right test was the property — *search results must not depend on insertion
order* — which is embedder-agnostic, runs offline, and catches both halves at once.

**BUG-12 — a latent crash the fix wave nearly activated.** While fixing BUG-7 the
implementer found that the INR rail already emitted `SETTLEMENT_FAILED` with no preceding
`INITIATED`, and `fold` raised `KeyError` on such a log. It was dormant — until BUG-9 and
BUG-10 started folding inside `execute_match`, which would have made it live. The fix wave
found a bug the fix wave itself was about to trigger.

---

## 4A. Performance and memory design — decided, mostly not built

A design discussion that produced more decisions than code. Recorded here so none
of it is lost, with an honest status on each. **Nothing in this section is
implemented yet.**

### The measurement that framed everything

The system rebuilds all state from the log on every read. Measured:

```
events in log  |  one rebuild
        500    |     2 ms      (x3 per trade =   6 ms)
      2,000    |     8 ms      (x3 per trade =  24 ms)
      8,000    |    26 ms      (x3 per trade =  78 ms)
     20,000    |    68 ms      (x3 per trade = 204 ms)
```

Three reads happen per trade (two on the rupee path): *how much has this buyer
spent*, *which order am I filling*, and *what is this buyer's points balance*.

**But a demo run is ~500 trades ≈ 2,500 events, so bookkeeping is ~25ms per trade
against 10–30 SECONDS of model calls — roughly 0.2% of runtime.** Optimising it
would save about four seconds across the entire run.

That number is the reason most of what follows is designed and deliberately not
built. **Trigger to revisit: ~20,000+ events, or bookkeeping exceeding ~5% of run
time.** Measure before building.

### Adopted — build in Plan 2

**The sticky note (incremental state + event offset).** Keep the current answers in
memory alongside the log line they are correct up to. Nothing new since? Answer
instantly. Five new events? Apply five. Never re-read from line 1. Cost becomes
proportional to what is new, not to total history.

Stores exactly three things, because these are what an agent needs to trade and
none of them may ever be stale: **points balance**, **spend against cap**, and
**units remaining per order**.

*The catch:* the answer now lives in two places, and a silently wrong balance is a
wrong money decision. The accountant is the fix — it periodically rebuilds from the
log, compares, and freezes on mismatch. Same mechanism as the Razorpay
reconciliation, which makes the accountant genuinely load-bearing rather than a
demo beat.

**Tiered memory as a real tree.** Not three boxes on a diagram — an actual tree
where each node carries its own freshness rule:

```
merchant_a/
├── balance, spend_headroom, orders/{id}/qty    ← exact, updated on write
└── counterparties/merchant_b/
    ├── reliability, haggles_hard               ← TTL, minutes
    └── history/lessons                         ← deep, rebuilt rarely
```

Three things this buys that a flat cache does not: freshness is enforced by the
node rather than remembered by the author; "descend only as far as you need"
becomes structural, so a routine trade reads one value and stops; and invalidation
cascades — one settled deal marks that counterparty's whole subtree stale.

Those leaves *are* the Subconscious's consolidated memory.

**TTL, but only above the money line.** A TTL says "probably still true for N
seconds," which is fatal for balances: agent has 1000 points, bids 800, wins, and
two seconds later the cache still says 1000 — a double-spend. Exact where it is
money, TTL where it is judgment. Using one mechanism for both is what makes either
one wrong.

**Context deltas with checkpoints.** Do not copy whole contexts between executions;
store the previous state plus a delta. **Deltas are additive-only on `facts` and
`decisions`** — the only field a delta may remove from is `unresolved_questions`,
because there removal *is* the semantics. A checkpoint that can drop a fact quietly
rewrites history.

Checkpoint on **episode boundaries** (a completed trade), not a fixed interval — it
is semantically meaningful, self-tuning, and is exactly what the Subconscious wants
to consolidate.

**Sub-agents narrow, they never merge.** Each of the three emits a structured
summary that becomes a *fact* in the orchestrator's delta. Narrowing is safe in a
way merging is not: you are choosing what to promote, not reconciling two versions
of the same thing. This sidesteps the "what is safe to merge" problem rather than
solving it, and it is what the isolated context windows were always for.

### Adopted — deferred until the trigger

**One min-heap per asset for the order book.** Better than the price-ordered tree
originally proposed, because the real query is *per asset, cheapest first* —
`find_candidates` groups by `asset_ref` and prices across different assets are not
comparable (₹18 for cardboard and ₹18 for serum have no shared order). Gives the
cheapest ask instantly, uses Python's built-in `heapq`, and has no rebalancing code
to get wrong.

*The catch:* an order that expires mid-heap cannot be plucked out — mark it dead and
discard it when it surfaces, or the book slowly fills with ghosts.

Currently a plain dict of a few dozen orders, which is fine.

### Rejected, and why

**Morris traversal.** Retired twice over. The tiered memory is not a binary search
tree, and heaps are not search trees either — an in-order walk of a heap is
meaningless. It *would* have been right for a price-ordered BST walked repeatedly by
the replay UI; that BST was rejected for a better reason. Worth remembering if the
book ever needs global price-ordered iteration.

**A price-ordered balanced BST.** Sorts a mixture with no natural order, and
hand-written rebalancing eats days for a memory saving of about twelve pointers at
our scale.

**A hand-built B+ tree index.** SQLite's indexes already *are* B-trees. Building one
would be re-implementing the database we are already using. Three `CREATE INDEX`
statements get the same thing free.

**Wall-clock cap on a single negotiation.** Produces unexplainable behaviour in the
audit trail — *"stopped after 60 seconds"* is not a reason a broker would ever give —
and makes runs depend on API latency rather than on agents. Time is the right bound
one level up: cap the whole market run.

### Negotiation: when an agent walks away

A hard round cap was the original design. It is wrong, and the constant currently in
`config.py` (`MAX_NEGOTIATION_ROUNDS = 4`) is an arbitrary number nothing consumes —
the only reference is a test asserting it equals what was typed.

Replaced by four layers:

| Layer | Stops it because | Appears as |
|---|---|---|
| **Reasoning** | Gap is not worth it, a better seller exists, the Subconscious says hold | The product. This is what is on screen. |
| **Progress** | The *gap between the two sides* has not moved in two exchanges | *"Neither side moved. Ending."* |
| **Backstop** | Token budget for this negotiation exhausted | Should never fire. If it does, it is a bug. |
| **Run** | Wall clock on the whole market run | Never visible inside a trade. |

Measure movement of the **gap**, not of each offer — otherwise oscillation
(₹19 → ₹20 → ₹19) reads as large movement and zero progress.

The backstop is a **token budget**, not a round count: tokens are the actual cost
being bounded, and the number lands in the log so runs stay reproducible.

Log the decision as an event carrying the agent's stated reason. That is both the
signal — far richer than a counter — and a good few seconds of video.

Also unresolved and needed before Plan 2: **the spec never defines whether a "round"
is one message or one exchange.** Twice the cost and twice the video length depending
on the answer.

---

## 4B. The memory architecture became Plan 2

The design in §4A stopped being a parked idea and became the spec for Plan 2:
`docs/superpowers/specs/2026-08-23-broker-and-memory-design.md`. That document holds
the design; this section holds the decisions behind it.

**Why this one earns its place when the order-book work did not.** The heap-versus-tree
argument was optimising 0.2% of runtime. Context architecture is not an optimisation of
the same kind — **context size is token cost**, and tokens are simultaneously the money
budget and the wall clock. Smaller, better-targeted context makes every model call
cheaper and faster. It hits the 99.8%, not the 0.2%. That distinction is the whole
reason one got built into a spec and the other got parked.

### Four changes made to the proposed architecture

**Checkpoints land on episode boundaries, not a fixed interval.** A completed trade is
already a meaningful boundary, it self-tunes with market activity, and it makes a
checkpoint mean *everything the broker knew when that deal closed* — which is precisely
what the Subconscious consolidates. Consolidation and checkpointing collapse into one
moment instead of two mechanisms that have to be kept in step.

**The "what is safe to merge" problem is dissolved, not solved.** Sub-agents never merge
contexts. Each promotes a structured summary that becomes a *fact* in the parent's
delta. Narrowing is safe in a way merging is not: you are choosing what to promote, not
reconciling two versions of the same thing. Executions branch downward and never rejoin,
so there is no diamond and no reconciliation. The isolated context windows were always
for this.

**Semantic compression gets a rule rather than good intentions.** Deltas are
additive-only on `facts` and `decisions`. The only field a delta may remove from is
`unresolved_questions`, because there removal *is* the semantics. A checkpoint that can
drop a fact quietly rewrites history — and worse, the agent has no way to know something
is missing.

**No hand-built B+ tree.** Correctly identified as an index rather than core
representation — and SQLite's indexes already are B-trees. Three `CREATE INDEX`
statements get point lookup, range query and insertion for free. Building one would be
re-implementing the database already in use.

### Scoping decisions, made in advance rather than under pressure

**If day 6 slips, the incremental state projection is dropped first.** It is worth ~0.2%
of runtime today and `fold()` already works correctly. **The Subconscious is not
droppable** — it is the differentiator, and a broker without memory is a chatbot with a
payment API.

Deciding this before the day arrives is deliberate: cuts made at 11pm on day 6 tend to
drop whatever is hardest rather than whatever matters least.

**A negotiation round is one message, not one exchange.** Plan 1's spec never said, and
the answer doubles or halves both token cost and screen time. Settled now because it
would otherwise be discovered as an argument mid-build.

**`Match` gains a `qty` field.** Without it a partial fill is unrepresentable, which is
one of the two defects carried out of Plan 1.

### Still open, with recommendations

1. **Which model per sub-agent?** Recommend small and fast for Trader/Scout/Diplomat,
   stronger for the orchestrator and the Subconscious where the judgment actually lives.
2. **How many brokers in the first run?** Recommend 3 to keep negotiation traces
   readable while debugging, then 6–8 for the recorded run.
3. **Does the Subconscious consolidate with a model call or a rule?** Recommend a model
   call — the entire value is distilling *"they haggle but always fold on delivery"*,
   which no rule expresses.

---

## 5. Standing risks

| Risk | Status |
|---|---|
| Disk at ~5GB free | Live. Model caches, simulation runs and video files all want room. |
| Production retrieval path has no automated coverage | Live, but reframed. The remedy is not a `default_embedder()` test — the escaped bug was in *fusion*, not the embedder. It is the order-invariance property test, now added, plus a network-marked smoke test asserting `default_embedder()` returns unit-norm vectors (still to add), since `_cosine` is a bare dot product that mis-ranks silently if that stops holding. |
| `fold()` requires a complete log from seq 1 | Live, now documented and pinned by a test. The replay UI must fold cumulative prefixes, never a sliding window. `fold_from(state, events)` is the real answer and belongs to the UI plan. |

### Carried into later plans — recorded, not fixed

The process allows one fix wave and no second. These are real and were surfaced rather
than silently patched. **The first two are defects, not merely risks.**

| # | Issue | Where it bites |
|---|---|---|
| 1 | `_record_fill` skips **both** sides when the bid order is absent from the book, so the *ask* stays open at full quantity. The re-settlement hazard BUG-9 closed survives on that path. | Plan 2, immediately. Fix first. |
| 2 | Both `ORDER_FILLED` events carry the *bid's* quantity and the *buyer* as actor, so the seller's fill is attributed to the buyer, and a partial fill of the bid is unrepresentable — `Match` has no `qty` field. | Plan 2, before negotiation lands. Needs a spec change. |
| 3 | **O(n²) fold.** Each `execute_match` now performs up to three full `read_all()` + `fold` passes. Over an hours-long offline run this is what will make the simulation crawl. | Plan 5. Must be planned before day 9, not discovered on it. |
| 4 | Denied matches appear in `state().matches`, since `MATCH_PROPOSED` precedes the gate by design. The accountant's "no orphaned matches" invariant must join against `POLICY_DECIDED` rather than treat presence as intent. | Plan 3. |
| 5 | The cumulative spend cap never decays, so an actor that reaches it is permanently spent out. A long tuning run will stall on this rather than on economics. | Plan 4. |
| 6 | `rrf_fuse` has no doc-id tie-break. Insertion-order invariance currently holds only because both input rankings are deterministic — it is consequential, not structural. One line makes it structural. | Any time. Cheap. |
| 7 | `REQUIRE_HUMAN` has no resume path — the match is simply dropped. The spec says it suspends pending approval, and video beat 13 depends on approve-then-settle. | Plan 2. Budget for it. |

---

## 5. Things that turned out better than expected

- **The natural-language storefront is not a second product.** A human typing "I need
  eco packaging under ₹20" is just a *descriptive bid* — the same object agents post,
  down the same retrieval and approval path. It became one input box over machinery that
  already existed, rather than the whole surface it looked like.
- **The accountant upgraded the failure demo.** The track requires one failure handled
  gracefully. Without it that is a declined card, which every team will show. With it,
  it is "the system noticed its own books were wrong, froze itself, repaired, resumed."
- **Selling intelligence found Razorpay a revenue line, not just the merchant.** The
  pitch stopped being "we grew merchant revenue" and became *Razorpay is the platform,
  merchants are the creators, their wins are the content, other merchants pay to watch,
  revenue is shared.*
