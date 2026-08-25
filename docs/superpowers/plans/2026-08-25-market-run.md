# Market Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a 30-merchant market against real Razorpay test-mode settlement until the event log contains a market worth replaying.

**Architecture:** Five separately-runnable scripts under `scripts/market/`, each reading and writing only the event log, so any of them can be re-run against an existing log without redoing the others' work. A roster of merchants with a trend planted in the *data*; a runner that drives real brokers through rounds; a payment clerk that drives the netbanking checkout so settlements actually complete; a house cycle that mines and auctions intelligence; and a failure injector.

**Tech Stack:** Python 3.11+, SQLite event log, DeepSeek via the OpenAI-compatible provider, Razorpay test mode, Claude-in-Chrome for payment automation.

**Spec:** `docs/superpowers/specs/2026-08-25-market-run-design.md`

## Global Constraints

- Python 3.11 or newer.
- The append-only event log is the single source of truth. All state is a projection folded from it.
- **Amounts are integers in minor units (paise). Never floats.**
- Every test injects a fake client. **No test may make a network call.** Scripts under `scripts/` may.
- `.env` holds live test-mode credentials. Never read, print, move, or commit it.
- Every script is **resumable**: re-running it against an existing log continues rather than duplicating.
- Every script is **bounded**: it has a ceiling on model spend and wall clock, checked before each model call, and stops cleanly leaving a resumable log.
- A failure a long run can hit — a Razorpay error, a model timeout, a malformed reply, a rate limit — becomes a recorded event the accountant can see. Never an exception that kills the run.
- **A value the checker must be authoritative about is never supplied by the party it constrains.** Eight bugs on this project came from violating this.
- Capture is discovered through the payment **link**, never the order. See `src/exchange/rails/capture.py`.

---

### Task 1: The roster

**Files:**
- Create: `scripts/market/__init__.py`
- Create: `scripts/market/roster.py`
- Test: `tests/test_roster.py`

**Interfaces:**
- Consumes: `exchange.models.Actor`, `ActorKind`, `Asset`, `AssetKind`, `Currency`
- Produces: `MERCHANTS: tuple[Merchant, ...]`, `Merchant` dataclass with fields
  `actor_id: str`, `name: str`, `category: str`, `persona: str`,
  `sells: tuple[Listing, ...]`, `needs: tuple[Need, ...]`;
  `Listing(asset_id, title, spec, ask_price, qty)`;
  `Need(round_no, text, qty, limit_price)`

**The point of this task.** The house agent's value is finding a cross-merchant pattern no single merchant can see. Thirty merchants with unrelated random needs contain no such pattern, so the agent would either report nothing or invent one. The roster therefore encodes real structure: a demand cluster whose need for a shared input **grows across rounds**, suppliers serving it, and unrelated merchants trading elsewhere so the signal has noise to be found in.

We plant the **cause**, never the conclusion. The agent gets no privileged access and must find the trend in the same aggregate activity everyone else sees. A judge can verify it against the log.

- [ ] **Step 1: Write the failing test**

```python
from collections import Counter

from scripts.market.roster import MERCHANTS, demand_by_round


def test_there_are_enough_merchants_to_clear_the_privacy_floor():
    """K_MIN is 25 DISTINCT contributors. Below it nothing can be minted,
    so nothing downstream exists."""
    from exchange.house.insights import K_MIN

    assert len(MERCHANTS) >= 30
    assert len({m.actor_id for m in MERCHANTS}) == len(MERCHANTS)
    assert len(MERCHANTS) > K_MIN, "leave room for merchants that never trade"


def test_the_planted_trend_actually_rises():
    """The trend must be IN the data, not asserted about it. If demand for the
    cluster's input does not really grow, the house agent has nothing true to
    find and would have to invent something."""
    demand = demand_by_round("cold brew concentrate")

    rounds = sorted(demand)
    assert len(rounds) >= 3
    assert demand[rounds[0]] < demand[rounds[-1]]
    assert all(demand[a] <= demand[b] for a, b in zip(rounds, rounds[1:]))


def test_the_trend_is_not_the_only_thing_happening():
    """A market where every merchant wants the same thing is not a market."""
    categories = Counter(m.category for m in MERCHANTS)

    assert len(categories) >= 4
    assert max(categories.values()) < len(MERCHANTS) // 2


def test_every_need_can_in_principle_be_met():
    """A need no merchant sells is dead weight: it burns model calls on a
    search that cannot succeed and teaches the Subconscious nothing."""
    sold = {listing.title.lower() for m in MERCHANTS for listing in m.sells}

    for merchant in MERCHANTS:
        for need in merchant.needs:
            assert any(word in title
                       for title in sold
                       for word in need.text.lower().split()
                       if len(word) > 4), need.text


def test_personas_differ():
    """Identical personas produce identical transcripts, which is a boring
    video and a useless market."""
    personas = {m.persona for m in MERCHANTS}

    assert len(personas) >= len(MERCHANTS) // 2
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/pytest tests/test_roster.py -v`
Expected: FAIL — `ModuleNotFoundError: scripts.market.roster`

- [ ] **Step 3: Write the roster**

Thirty-plus merchants as data. Required shape:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Listing:
    asset_id: str
    title: str
    spec: dict
    ask_price: int   # paise per unit
    qty: int


@dataclass(frozen=True)
class Need:
    round_no: int
    text: str
    qty: int
    limit_price: int  # paise per unit


@dataclass(frozen=True)
class Merchant:
    actor_id: str
    name: str
    category: str
    persona: str
    sells: tuple[Listing, ...]
    needs: tuple[Need, ...]


def demand_by_round(input_text: str) -> dict[int, int]:
    """Total quantity sought for an input, per round, across all merchants.

    The planted trend has to be measurable from the roster itself — that is
    what makes it real rather than claimed.
    """
    totals: dict[int, int] = {}
    for merchant in MERCHANTS:
        for need in merchant.needs:
            if input_text.lower() in need.text.lower():
                totals[need.round_no] = totals.get(need.round_no, 0) + need.qty
    return totals
```

Compose the roster so that:
- **The cluster:** 8–10 beverage merchants in one city whose need for *cold brew concentrate* grows over rounds 1→4 (e.g. total qty 400 → 900 → 1600 → 2600). Their needs must be genuinely different in wording so retrieval has to work rather than string-match.
- **The suppliers:** 3–4 merchants selling cold brew concentrate at different prices and qualities, so `choose()` has a real decision and the Diplomat's advice matters.
- **The noise:** the remaining merchants in at least three other categories (packaging, textiles, electronics accessories, dry goods) with their own needs and listings, some of which also trade with each other.
- **Personas:** vary the negotiating style in one sentence each — "opens low and concedes slowly", "walks rather than overpay", "values delivery certainty over price". These are prompt text, not weights.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_roster.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/market/__init__.py scripts/market/roster.py tests/test_roster.py
git commit -m "feat(market): a 30-merchant roster with a real trend planted in it"
```

---

### Task 2: Seeding the market

**Files:**
- Create: `scripts/market/seed.py`
- Test: `tests/test_market_seed.py`

**Interfaces:**
- Consumes: `roster.MERCHANTS`, `Exchange.register_actor`, `Exchange.list_asset`, `Exchange.post_order`
- Produces: `seed(exchange, merchants) -> SeedReport` with `registered: int`, `listed: int`, `skipped: int`

**Resumability is the whole task.** Seeding twice must not create thirty duplicate actors or double the order book. The log already knows who is registered; read it and skip.

- [ ] **Step 1: Write the failing test**

```python
def test_seeding_twice_registers_nobody_twice(exchange):
    from scripts.market.roster import MERCHANTS
    from scripts.market.seed import seed

    first = seed(exchange, MERCHANTS)
    second = seed(exchange, MERCHANTS)

    assert first.registered == len(MERCHANTS)
    assert second.registered == 0
    assert second.skipped == len(MERCHANTS)
    assert len(exchange.state().actors) == len(MERCHANTS)


def test_seeding_twice_does_not_double_the_book(exchange):
    from scripts.market.roster import MERCHANTS
    from scripts.market.seed import seed

    seed(exchange, MERCHANTS)
    asks_after_first = len([o for o in exchange.state().open_orders.values()
                            if str(o.side) == "ASK"])
    seed(exchange, MERCHANTS)
    asks_after_second = len([o for o in exchange.state().open_orders.values()
                             if str(o.side) == "ASK"])

    assert asks_after_second == asks_after_first


def test_a_fresh_exchange_over_a_seeded_log_can_match(tmp_path):
    """The index is rebuilt from the log, so a resumed run is not blind."""
    # build one exchange, seed it, close it, open another over the same db,
    # and assert find_candidates returns something for a roster need
```

- [ ] **Step 2: Run and watch it fail**

- [ ] **Step 3: Implement `seed`**

Register each merchant not already in `exchange.state().actors`. List each asset not already present. Post each merchant's round-1 asks. Return counts. Never raise on an already-seeded log.

- [ ] **Step 4: Run the tests**

- [ ] **Step 5: Commit**

---

### Task 3: The payment clerk

**Files:**
- Create: `scripts/market/clerk.py`
- Test: `tests/test_clerk.py`

**Interfaces:**
- Consumes: the event log; `exchange.rails.capture.find_captured_payment`
- Produces: `pending_payments(log) -> list[PayableSettlement]` with
  `settlement_id`, `payment_link_id`, `payment_link_url`, `amount`, `actor_id`

**Why this exists.** Established by probing live test mode: a payment **cannot** be created server-side (`403` on the S2S endpoint), so a settlement only completes when someone actually pays its link. Without this, every INR settlement stays PENDING, no order is ever filled, no points are minted, confidence stays at zero, and the house agent mines an empty set — the entire run produces nothing.

**This task ships only the read side.** It answers "what still needs paying?" from the log. The browser automation that pays them is driven separately, by the operator, because it needs a live Chrome session — and it must be re-runnable without paying anything twice.

Also verified live: **cards are rejected on this account** (*"International cards are not supported"*); **netbanking succeeds** and is the better automation target — fewer fields and a deterministic result page.

- [ ] **Step 1: Write the failing test**

```python
def test_only_unpaid_settlements_are_listed(log):
    """A settlement already COMPLETED must never be offered for payment
    again: paying twice is real money moving twice."""


def test_a_settlement_without_a_link_is_not_payable(log):
    """A link that failed to create leaves an order nobody can pay through
    this route. It is reported as unpayable, not silently dropped —
    the run's post-mortem needs to know."""


def test_the_clerk_reads_the_link_not_the_order(log):
    """The order recorded at settlement time cannot receive a payment."""
```

- [ ] **Step 2–5:** implement, test, commit.

---

### Task 4: The market runner

**Files:**
- Create: `scripts/market/run.py`
- Test: `tests/test_market_run.py`

**Interfaces:**
- Consumes: `Broker.find_supply/assess/choose/close`, `negotiate`, `seed`
- Produces: `run_round(exchange, brokers, merchants, round_no, budget) -> RoundReport`

**The budget is a policy gate for model spend.** We spent four fix waves making every money action bounded and gated; a runner that can spend without a ceiling is the same defect in a different currency. `budget` is checked **before** each model call, and exhausting it ends the round cleanly with a resumable log — not an exception.

- [ ] **Step 1: Write the failing test**

```python
def test_a_round_that_exhausts_its_budget_stops_cleanly(exchange):
    """Not an exception, and not a partial write that cannot be resumed."""


def test_a_broker_whose_model_call_fails_does_not_stop_the_round(exchange):
    """One merchant's bad turn is not the market's problem. With 30 brokers
    over two hours, something will fail; the run must survive it and record
    what happened."""


def test_re_running_a_completed_round_does_no_work(exchange):
    """Resumption after an interrupted run must not re-trade round 1."""


def test_every_trade_is_gated_before_it_settles(exchange):
    """The invariant the whole project is judged on, asserted over a whole
    round rather than a single trade."""
```

- [ ] **Step 2–5:** implement, test, commit.

---

### Task 5: The house cycle

**Files:**
- Create: `scripts/market/house_cycle.py`
- Test: `tests/test_house_cycle.py`

**Interfaces:**
- Consumes: `HouseAgent.observe/mint_from/feed`, `run_auction`, `settle_purchase`, `pay_royalties`, `Broker.value_insight`
- Produces: `run_house_cycle(exchange, house, brokers, correlation_id) -> CycleReport`

Separately runnable so intelligence can be re-minted from an existing log **without re-trading** — which is what tuning means in practice.

- [ ] **Step 1: Write the failing test**

```python
def test_the_cycle_refuses_below_the_privacy_floor(exchange):
    """And says so legibly. A visible privacy refusal is a good thing to
    show on camera; a crash is not."""


def test_a_cleared_auction_pays_its_contributors(exchange):
    """Points conservation across the whole cycle: what the winner spent
    equals what contributors and the house received."""


def test_an_unreadable_valuation_is_not_a_bid_of_zero(exchange):
    """Two unreadable replies must not let the third win at price zero."""
```

- [ ] **Step 2–5:** implement, test, commit.

---

### Task 6: The failure injector

**Files:**
- Create: `scripts/market/inject_failure.py`
- Test: `tests/test_inject_failure.py`

**Interfaces:**
- Consumes: `Accountant.reconcile/freeze/repair/resume`
- Produces: `inject_drift(log, settlement_id) -> None`

**This is the track's required "one failure handled gracefully", on real data.** Today it exists only in tests. It must use the same reconcile → freeze → repair → resume path as production, with nothing bypassed, and the whole arc must read on the trade's own correlation id:

```
SETTLEMENT_INITIATED → DRIFT_DETECTED → ACTOR_FROZEN
→ SETTLEMENT_COMPLETED → ACTOR_RESUMED
```

Deliberately a **separate script**, so the recorded failure is visibly a detection rather than a scripted scene.

- [ ] **Step 1: Write the failing test**

```python
def test_the_whole_arc_reads_on_the_trades_own_thread(log):
    """Pin the trade and the story is complete and in order."""


def test_the_repair_records_razorpays_payment_id_not_an_invented_one(log):
    """The remote is the authority for whether money moved."""


def test_a_frozen_merchant_cannot_trade_until_resumed(exchange):
    """The freeze binds. It was decorative for four review passes."""
```

- [ ] **Step 2–5:** implement, test, commit.

---

### Task 7: The operator entrypoint

**Files:**
- Create: `scripts/market/main.py`
- Modify: `README.md`

Ties the phases together behind one command with explicit flags
(`--seed`, `--rounds N`, `--house`, `--inject-failure`, `--budget`, `--dry-run`),
each independently runnable and resumable. Prints a legible progress line per
round: merchants acted, trades settled, points minted, spend so far.

`--dry-run` runs the whole flow against a scripted provider and a fake
Razorpay client, so the wiring can be exercised for free before spending
anything.

- [ ] **Steps:** implement, document the operator runbook in the README
  (including that payment requires a live Chrome session and uses netbanking,
  not cards), commit.

---

## Acceptance

The plan is done when a run produces a log where all four hold:

1. **25+ distinct merchants have settled a real trade.** Below the privacy floor nothing downstream exists.
2. **The house agent finds a pattern that is genuinely there** — verifiable against the log, not written into a fixture.
3. **An insight auction clears second-price with real bids and moves points**, royalties reaching contributors.
4. **The failure path occurs on real data**, and the whole arc reads on one correlation id.

A run that lacks any of these is a failed run, however clean the code.
