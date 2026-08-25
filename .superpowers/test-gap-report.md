# Test gap closed, and three defects decided — `feat/market-run`, 2026-08-25

Suite: **394 → 423 passing**, 29 new tests, no network call, every provider and
Razorpay client injected. `src/exchange/rails/capture.py`, `src/exchange/rails/inr.py`,
`FakeRazorpay` and `AlwaysCaptured` are byte-identical to what they were at the
start (`git diff --stat` names neither rail file).

---

## Part 1 — the three fixes wave E shipped without tests

Each was written, then run against the pre-fix source (`git show 9a0aa05:<path>`
into place, run, restore). **All three were verified this way; none was
unverifiable.**

### (a) The negotiation loop's hard call bound — `tests/test_negotiation.py`

Six tests. The stub is `ZeroUsageProvider`: it constructs a real frozen
`LLMResponse(text, input_tokens=0, output_tokens=0, model=...)` — literally what
`openai_compat` produces from a response with no `usage` block — and it never
converges (unparseable replies, so no offer is appended and `gap_stalled` cannot
fire either).

- `test_the_stub_really_does_report_zero_usage` exists so the other tests cannot
  pass on the token budget by accident. A stub that quietly charged a token or
  two would leave the cap untested — the "fake kinder than the real thing"
  failure this project has already paid for twice.
- `test_a_provider_that_reports_no_tokens_still_terminates` — stops at exactly
  `MAX_MODEL_CALLS`.
- `test_the_cap_ends_the_negotiation_as_a_result_not_as_an_exception` — the
  walk-away shape (`agreed=False`, `final_price=None`, a reason that says why)
  and a `NEGOTIATION_ENDED` in the journal. Never an exception, never silence.
- `test_the_cap_is_counted_on_calls_not_on_a_figure_the_provider_supplies`
- `test_a_healthy_negotiation_is_untouched_by_the_cap`
- `test_a_zero_token_call_is_charged_rather_than_treated_as_free` — `_ASSUMED_TOKENS`.

The stub carries a tripwire at 200 calls so a build without the cap **fails in
finite time instead of hanging**. Against `9a0aa05` that is exactly what
happened: `AssertionError: runaway negotiation: 201 model calls with no
termination`. 4 of the 6 failed pre-fix; the healthy-negotiation test and the
stub-honesty test passed in both worlds, which is correct — they must be
unaffected.

### (b) Resumability of the retrieval index — `tests/test_service.py`

- `test_a_fresh_exchange_matches_asks_listed_by_a_previous_one` — Exchange 1
  seeds and matches; the log is closed; a **new `EventLog` + new `Exchange` +
  new `HybridIndex`** over the same file finds the same ask.
- `test_a_fresh_exchange_rebuilds_the_index_from_the_log` — the mechanism,
  asserted directly (`index.size == 2`).
- `test_indexing_cost_is_linear_in_listings_not_quadratic` — a
  `CountingEmbedder` tallies **texts embedded**, not milliseconds: 30 listings
  must embed 30 texts, and the failure message names both the linear figure and
  the quadratic one (465).
- `test_relisting_an_asset_does_not_duplicate_it_in_the_index` — a runner that
  re-lists its inventory on resume is idempotent.

Pre-fix (`9a0aa05` `service.py`, where the index was `self._indexed: list` and
`list_asset` re-`index()`ed the whole catalogue): **all four fail**, the resume
ones on 0 candidates / `size == 0`, the cost one on 2 embeds for 2 listings of
the same asset.

### (c) A failed Razorpay capture poll — `tests/test_rails.py`, `tests/test_accountant.py`

`RefusingLookup` and `RefusingOne` **wrap `FakeRazorpay`** rather than replacing
it, so every path except the refused one keeps the behaviour that was verified
against live test mode. Both lookup doors raise (`payment_link.fetch` and
`order.payments`), because a capture is found through the link and falls back to
the order — leaving either open would test a path the run does not take.

Rail (5 tests): settlement stays `PENDING`, nothing propagates,
`CAPTURE_POLL_FAILED` is written naming the settlement and the 429, no
`SETTLEMENT_FAILED` is written (that would tell the accountant the exposure
closed — it did not), and one rejection **stops** the polling rather than
retrying into the limit.

Service (1 test): through `execute_match`, the `ALLOW` still resolves to a
`PENDING` settlement instead of a dead process.

Accountant (5 tests): the sweep continues past a rejected lookup and checks
everything after it; the rejection is reported as `unchecked`, never invented as
a drift; it lands on the **trade's own** correlation as `RECONCILE_CHECK_FAILED`;
nothing is confirmed by a failed look so the watermark re-checks it next pass;
and `clean is True` while `unchecked` is non-empty — "no disagreement I could
see" is not "the pass finished".

Pre-fix (`9a0aa05` `inr.py` + `accountant.py`): **all 10 fail**, each with the
raw `RuntimeError: 429 Too Many Requests` escaping.

---

## Part 2 — the three audit defects

### (d) MEDIUM — `_log_refusal` under one `action_ref`

**Already fixed and already tested**, in `78901e9` itself — `broker.py:373-377`
refuses a `match_id` already in `decided_action_refs`, and
`tests/test_broker.py:690,707` pin both halves (a second refusal raises; a
`matching.resize` retry is decided on its own terms). Wave E's commit message
undersells what it shipped. Nothing to do there, and the retry test is untouched.

What the audit asked for and was **missing** is the auditor's half: §9's
`duplicate_decision` invariant. Added at `accountant.py:556-585` and covered by
three tests. It recomputes the property from a full read of the log — the same
backstop shape as `duplicate_mint` — so a decision that slips past either guard
is *named* rather than hidden, and it counts decisions **whoever wrote them**,
because a merchant may legitimately write a `POLICY_DECIDED` and a check that
only counted the gate's own would miss the half that was reachable.

### (e) MEDIUM — the cumulative spend cap. **Decision: windowed.**

Implemented, because it was clean from the log — the events carry timestamps and
nothing else had to move.

- `ExchangeState.spend_to_date` (a scalar) became `spend_ledger`:
  `actor -> currency -> ((ts, amount), ...)`. **A running total is the one shape
  a window cannot be recovered from** — the scalar had thrown away exactly the
  fact the window needs. `ts` is the *envelope's*, stamped by `EventLog.append`.
- `spend_to_date` survives as a **derived property** summing the ledger. It is
  reporting only, it cannot drift from the ledger, and it is not a dataclass
  field, so it plays no part in the equality the accountant's `projection_drift`
  check rests on. The three existing projection tests still pass unchanged
  through it.
- `PolicyLimits.rolling_window_seconds = 3600`, beside the figure it qualifies:
  a cap without its period is not a cap, and it was read as "for life" for
  exactly as long as the field was missing.
- `Exchange._spend_to_date(actor, currency, window_seconds)` sums only entries
  inside the window.

**Why one hour.** A typical trade is ~388,000 paise (1,940 × 200) and a merchant
makes roughly ten in a 1–2 hour run: ~3.9 lakh an hour against a ceiling of 10
lakh. Comfortable in a healthy run, and a broker that starts looping still hits
the cap within the hour. It is also short enough that a tuning re-run started
straight after the last one inherits **at most one hour** of history, which then
decays — instead of inheriting everything forever. Three full runs inside one
window is not physically possible at 1–2 hours a run.

**The gate still derives the figure itself**, and all three inputs:
- the **amounts** come off `SETTLEMENT_INITIATED` events the rail wrote;
- the **timestamps** are the ones `EventLog.append` stamped;
- **`now`** is read inside `_spend_to_date`;
- the **window** is configuration on the `Exchange` (`PolicyLimits`), set once at
  wiring time, for the same reason the caps are — `execute_match` takes no
  window and no limits, and `test_the_window_is_configuration_not_something_the_caller_can_widen`
  asserts that on the signature *and* behaviourally, with a caller that lies
  `rolling_spend=0` and is still denied at 150,000.

An **unparseable timestamp counts against the cap** (`_parse_ts` returns
`datetime.max`), not away from it. An unreadable ts is not evidence a merchant
has room; wrong in the direction of refusing is the only direction a cap may be
wrong in. Tested.

The DENY now names the period and says the room comes back:
`"Amount X would breach the rolling window cap Y over the last 1h (spent Z in
that window; it frees up as the oldest of those settlements ages out)"`. The
phrase "rolling window" is preserved so the existing assertion at
`tests/test_service.py:217` still holds.

Five new tests: aged-out spend does not bind, in-window spend still does, the
DENY names the window, the window is not the caller's to widen, an unreadable ts
counts. The backdating helper `_append_at` **INSERTs** a row with a chosen ts —
INSERT is permitted, only UPDATE and DELETE raise, so the append-only guarantee
is untouched; an existing row is never re-dated.

### (f) LOW — re-indexing

Already fixed by Part 1(b)'s `_sync_index` / `HybridIndex.add`. It had **no
test**; `test_indexing_cost_is_linear_in_listings_not_quadratic` and
`test_relisting_an_asset_does_not_duplicate_it_in_the_index` are now that test,
and both fail against `9a0aa05`.

---

## New findings — the three shapes I was asked to watch for

### 1. `Accountant.repair` is the twin that was not converted. **Both shapes at once.**

`src/exchange/house/accountant.py:756`

```python
for item in self._client.order.payments(order_id).get("items", []):
```

where `order_id = initiated.payload["razorpay_order_id"]` — the **receipt**
order, the one `inr.py:155` records. `reconcile` was moved to the shared
`find_captured_payment` (`accountant.py:251`); `repair`, forty lines further
down, still asks the raw order and ignores the `payment_link_id` sitting in the
same payload. `capture.py`'s own probed docstring says what that returns:
*"Polling `order.payments(our_order)` returns `{"count": 0, "items": []}`
permanently, however many payments are made."*

So **`reconcile` finds the drift and `repair` refuses it.** Reproduced against
the merged source with a client that models live test mode (receipt order stays
empty; the capture sits under the order the paid link minted):

```
settled:            PENDING
reconcile drifts:   ['stl_2bc0beb5050f']
repair RAISED:      ValueError refusing to complete stl_...: no captured
                    payment on order_RECEIPT
```

That is the failure demo — the track's required *"one failure handled
gracefully"*, step 11 of the video — dying at the repair.

It is green in the suite only because `FakeRazorpay._Orders.payments`
(`tests/test_rails.py:83-89`) also answers for the receipt id, deliberately, so
that tests writing settlements straight into the log work. **A fake kinder than
the real API, hiding a bug in one of two twin functions** — both of today's
lessons, in one place. Not fixed: out of the six items I was given, and *which
id repair should ask about* is the same decision `capture.py` exists to own,
which I was told not to touch. One line: route it through
`find_captured_payment(self._client, payment_link_id=initiated.payload.get("payment_link_id"), razorpay_order_id=order_id)`.
It needs a test with a client that does **not** answer for the receipt order.

### 2. The eighth instance is still open, and it is the only `PolicyContext` field the gate does not re-derive.

`src/exchange/agents/broker.py:439` — `counterparty_confidence=self.graph.confidence(seller_id)`

`policy.evaluate` binds `unknown_counterparty_cap` — the entire anti-incumbency
mechanism, the trial-size bound — on this number, and it is supplied by the
party it constrains, from `RelationshipGraph`, which is broker-local process
memory that is not folded from the log and that the accountant cannot check. The
two fields beside it are discarded and re-derived precisely because a figure the
actor supplies is not a bound; this one is not. It is honestly documented as a
known gap at `broker.py:433-438`, so this is a reminder of its rank, not a new
discovery: it is *a value the checker must be authoritative about, supplied by
the party it constrains*, on the money path, live today. Also unaffected by the
window work — the confidence gap is orthogonal to the spend cap.

### 3. Nothing else of the three shapes.

I checked the auction path (`auction.py:87-111`: `Bid.amount` is the bidder's,
but `settle_purchase` routes through `execute_match`, so the bound that matters
is the gate's and the credit rail's balance check, both derived), the mint basis
(`_counterparty_ask_price` — all four checks present, and it is the model the
others should follow), `_posted_bid_limit` (fixed in wave E, its comment now
true), `_status_of`, `_already_decided` and `_minted_settlement_ids` (all derived
by the checker, from the log). No further twin was left half-fixed that I could
find.

---

## Note on the working tree

`scripts/market/` and `tests/test_roster.py` appeared untracked mid-session,
written by something else working in this repo. `tests/test_roster.py` cannot be
collected (`ModuleNotFoundError: scripts.market.roster`), so my suite runs used
`--ignore=tests/test_roster.py`. Neither file is mine and neither is committed.
