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

## 5. Standing risks

| Risk | Status |
|---|---|
| Disk at ~5GB free | Live. Model caches, simulation runs and video files all want room. |
| Production retrieval path has no automated coverage | Live. Every test injects a fake embedder; `default_embedder()` is only ever exercised by hand. This is what hid §3.3. |
| `fold()` requires a complete log from seq 1 | Live. `read_since(seq)` returns partial slices, and folding one raises `KeyError`. The replay UI must fold cumulative prefixes, never a sliding window, unless a snapshot mechanism is designed first. |
| `SETTLEMENT_FAILED` transition untested in the projection | Deferred. Covered at the rail level, not in `fold`. |
| `numpy` used but not declared | Deferred. Arrives transitively via model2vec; one-line fix. |

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
