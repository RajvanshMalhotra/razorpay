# Intelligence Economy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Razorpay's cross-merchant view into a product — a house agent that mines settled activity into market intelligence, publishes free headlines, and auctions the details to broker agents for points — and change how brokers decide, so no hand-set weight picks a counterparty or prices a lot.

**Architecture:** Agents choose, the gate bounds. `find_candidates` returns a retrieval-ranked shortlist; an agent picks from it with reasons. The house agent mints `InsightLot`s past a mechanical privacy floor and clears sealed second-price auctions in points. The accountant reconciles against Razorpay, mints points, freezes on drift — and its reconciliation is the delivery signal the reliability half of the memory loop has been waiting for.

**Tech Stack:** Python 3.11+, SQLite, `openai` SDK (Ollama local / DeepSeek production), `razorpay`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-23-intelligence-economy-design.md`

## Global Constraints

- Python 3.11 or newer.
- Razorpay **test mode only**. Keys in gitignored `.env`.
- The event log is the single source of truth; all other state is a projection folded from it.
- Every money action emits a `PolicyDecision` **before** executing. Brokers reach money only via `Exchange.execute_match`.
- Amounts are integers in minor units. Never floats. Scores and confidences are floats.
- **Every test injects a scripted LLM provider. No test may make a network call.**
- Context deltas are additive-only on `facts` and `decisions`; only `unresolved` is removable.
- `Asset.kind == INSIGHT` implies `currency == CREDITS`.
- `K_MIN = 25` — no insight lot may derive from fewer merchants.
- **Points are minted only by the accountant.** Nothing else may mint.
- **No agent may set a limit.** Judgment picks a number; a hard cap decides whether it is allowed.
- The task ends with a commit.

---

### Task 1: Carried fixes from Plan 2

Two defects recorded in the Plan 3 spec §9. The choosing agent built in Task 3 needs all three sub-agent summaries, and a negotiated price must not be optional.

**Files:**
- Modify: `src/exchange/agents/broker.py`
- Test: `tests/test_broker.py`

**Interfaces:**
- Consumes: `Broker`, `ContextTree`, `ContextDelta` from Plan 2.
- Produces: `Broker.assess` and `Broker.find_supply` both promote their sub-agent's reply to the broker's own chain; `close` requires `agreed_price`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_broker.py`:

```python
def test_every_sub_agent_summary_reaches_the_orchestrator(exchange):
    """Spec 4.2: each sub-agent narrows upward. Only the Trader's did."""
    broker = Broker("m_buyer", exchange,
                    ScriptedProvider(["trader says supply is tight",
                                      "diplomat says try them small"]))

    broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")
    broker.assess("m_seller", "c1")

    root = broker.tree.materialise(broker.root_id)
    assert any("supply is tight" in f for f in root.facts)
    assert any("try them small" in f for f in root.facts)


def test_the_promoted_chain_is_visible_to_the_sub_agents(exchange):
    """A promoted fact the sub-agents cannot see is not shared context."""
    broker = Broker("m_buyer", exchange,
                    ScriptedProvider(["supply is tight", "second call"]))
    broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    trader_sees = broker.tree.materialise(broker._trader.node_id)

    assert any("supply is tight" in f for f in trader_sees.facts)


def test_close_requires_the_negotiated_price(exchange):
    """Optional meant a caller could silently settle at the ask again."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    with pytest.raises(TypeError):
        broker.close(matches[0], "m_seller", "c1")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_broker.py -k "summary or promoted or requires_the_negotiated" -v`
Expected: FAIL — only the Trader promotes, and `close` accepts a missing price.

- [ ] **Step 3: Promote every sub-agent reply through one helper**

In `src/exchange/agents/broker.py`, add a private method and use it from both `find_supply` and `assess`:

```python
    def _promote(self, summary: str) -> None:
        """Narrow a sub-agent's reply into the broker's own context.

        Sub-agents branch from `root_id` and never merge; each returns a
        structured summary that becomes a fact here. Re-parenting the three
        agents onto the new node is what keeps the promoted facts visible to
        them — otherwise the orchestrator accumulates a chain its own workers
        cannot read.
        """
        self.root_id = self.tree.add(
            self.root_id,
            ContextDelta(facts_added=(summary,)),
            len(self._exchange.log.read_all()),
        )
        for agent in (self._trader, self._scout, self._diplomat):
            agent.reparent(self.root_id)
```

Add to `SubAgent` in `src/exchange/agents/subagents.py`:

```python
    def reparent(self, parent_id: str) -> None:
        """Re-root this agent's next action under a new shared ancestor.

        Called when the orchestrator promotes a fact, so the agent's later
        actions see it. Its own prior branch is left intact and unreachable —
        history is never rewritten, only re-anchored.
        """
        self.node_id = self._tree.add(parent_id, ContextDelta(), self._state_version)
```

Then in `find_supply`, replace the discarded Trader call with `self._promote(self._trader.act(...))`, and in `assess`, capture the Diplomat's reply, promote it, and return it.

- [ ] **Step 4: Make `agreed_price` required**

Change the signature to `agreed_price: int` with no default, and delete the `if agreed_price is not None:` guard — `match = replace(match, clearing_price=agreed_price)` unconditionally. Update every caller in `tests/` and `scripts/`.

- [ ] **Step 5: Run the suite**

Run: `.venv/bin/pytest -q`
Expected: all pass, count risen by 3.

- [ ] **Step 6: Commit**

```bash
git add src/exchange/agents/ tests/test_broker.py scripts/
git commit -m "fix: promote every sub-agent summary, and require the negotiated price"
```

---

### Task 2: Per-tier LLM providers

The Subconscious and the choosing agent get the strong tier; the narrow roles get the fast one. Cost is not the constraint — the expensive model is the rarest call — so this is about where judgment lives.

**Files:**
- Modify: `src/exchange/llm/openai_compat.py`
- Modify: `src/exchange/agents/broker.py`
- Modify: `.env.example`
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces: `exchange.llm.openai_compat.providers_from_env() -> tuple[LLMProvider, LLMProvider]` returning `(strong, fast)`; `Broker(actor_id, exchange, provider, fast_provider=None, ...)` where `fast_provider` defaults to `provider`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm.py`:

```python
def test_providers_from_env_returns_a_strong_and_a_fast_tier(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL_STRONG", "deepseek-v4-pro")
    monkeypatch.setenv("LLM_MODEL_FAST", "deepseek-v4-flash")

    strong, fast = providers_from_env()

    assert strong._model == "deepseek-v4-pro"
    assert fast._model == "deepseek-v4-flash"


def test_both_tiers_fall_back_to_one_model_when_unset(monkeypatch):
    """Local development points both tiers at whatever Ollama has."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("LLM_MODEL_STRONG", raising=False)
    monkeypatch.delenv("LLM_MODEL_FAST", raising=False)
    monkeypatch.setenv("LLM_MODEL", "llama3.2:latest")

    strong, fast = providers_from_env()

    assert strong._model == fast._model == "llama3.2:latest"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_llm.py -k providers_from_env -v`
Expected: FAIL — `providers_from_env` undefined.

- [ ] **Step 3: Implement**

In `src/exchange/llm/openai_compat.py`, add below `provider_from_env`:

```python
def providers_from_env() -> tuple[OpenAICompatProvider, OpenAICompatProvider]:
    """Return (strong, fast).

    The strong tier carries judgment — consolidating an episode, choosing a
    counterparty, valuing an insight. The fast tier carries the narrow roles.
    The expensive model is the rarest call, so mixed tiering costs less than
    running one tier everywhere, not more.

    LLM_MODEL_STRONG / LLM_MODEL_FAST override per tier; both fall back to
    LLM_MODEL so local development needs no extra configuration.
    """
    base = provider_from_env()
    strong_model = os.environ.get("LLM_MODEL_STRONG")
    fast_model = os.environ.get("LLM_MODEL_FAST")
    if not strong_model and not fast_model:
        return base, base
    return (
        _retier(base, strong_model) if strong_model else base,
        _retier(base, fast_model) if fast_model else base,
    )


def _retier(base: OpenAICompatProvider, model: str) -> OpenAICompatProvider:
    """A sibling provider on the same endpoint and key, different model."""
    clone = OpenAICompatProvider.__new__(OpenAICompatProvider)
    clone._client = base._client
    clone._model = model
    return clone
```

- [ ] **Step 4: Take a fast tier in the broker**

In `Broker.__init__`, add `fast_provider=None` after `provider` and set `fast = fast_provider or provider`. Give the Subconscious `provider` (strong) and the three sub-agents `fast`.

- [ ] **Step 5: Extend `.env.example`**

```bash
# Optional per-tier models. Both fall back to LLM_MODEL when unset.
LLM_MODEL_STRONG=
LLM_MODEL_FAST=
```

- [ ] **Step 6: Run the suite and commit**

```bash
.venv/bin/pytest -q
git add src/exchange/llm/ src/exchange/agents/broker.py .env.example tests/test_llm.py
git commit -m "feat: per-tier providers, judgment on the strong model"
```

---

### Task 3: Agents choose from a shortlist

Delete the counterparty weight. `find_candidates` ranks by retrieval relevance only; the broker asks an agent to pick, and the choice plus its reasoning land in the log.

**Files:**
- Modify: `src/exchange/matching.py`
- Modify: `src/exchange/agents/broker.py`
- Modify: `src/exchange/events.py`
- Test: `tests/test_matching.py`, `tests/test_broker.py`

**Interfaces:**
- Produces: `find_candidates(bid, asks, assets, index, top_k=3) -> list[Match]` — no `counterparty_scores`; `Broker.choose(matches, correlation_id) -> Match`; event constant `COUNTERPARTY_CHOSEN`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_matching.py`:

```python
def test_matching_no_longer_takes_counterparty_scores():
    """Standing is a fact for the agent, not a multiplier on a score."""
    import inspect

    assert "counterparty_scores" not in inspect.signature(find_candidates).parameters


def test_ranking_is_relevance_only():
    asks = [_ask("ord_1", "m_a", "ast_1", 1800), _ask("ord_2", "m_b", "ast_2", 1940)]

    matches = find_candidates(BID, asks, ASSETS, _index())

    assert matches[0].ask_order_id == "ord_2"  # the semantic match, not the cheaper ask
```

Append to `tests/test_broker.py`:

```python
def test_the_agent_picks_from_the_shortlist_and_says_why(exchange):
    broker = Broker("m_buyer", exchange,
                    ScriptedProvider(["trader ok", "I pick 1: never missed a delivery"]))
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    chosen = broker.choose(matches, "c1")

    assert chosen in matches
    events = [e for e in exchange.log.read_by_correlation("c1")
              if e.type == "COUNTERPARTY_CHOSEN"]
    assert len(events) == 1
    assert "never missed a delivery" in events[0].payload["reason"]


def test_an_unparseable_choice_falls_back_to_the_top_of_the_shortlist(exchange):
    """A model that will not answer must not stop the market."""
    broker = Broker("m_buyer", exchange,
                    ScriptedProvider(["trader ok", "I have no opinion whatsoever"]))
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")

    chosen = broker.choose(matches, "c1")

    assert chosen is matches[0]


def test_choosing_from_one_candidate_makes_no_model_call(exchange):
    """A shortlist of one is not a choice."""
    provider = ScriptedProvider(["trader ok"])
    broker = Broker("m_buyer", exchange, provider)
    matches = broker.find_supply("biodegradable compostable mailers", 200, 2200, "c1")
    before = len(provider.calls)

    broker.choose(matches[:1], "c1")

    assert len(provider.calls) == before
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_matching.py tests/test_broker.py -k "counterparty_scores or relevance_only or picks_from or unparseable or one_candidate" -v`
Expected: FAIL.

- [ ] **Step 3: Strip the weight from matching**

In `src/exchange/matching.py`, delete `COUNTERPARTY_WEIGHT`, the `counterparty_scores` parameter, the `standing` lookup and the `final_score` multiplication. Score is the retrieval score. Keep the price tie-breaker in the sort and drop the standing clause from the rationale.

- [ ] **Step 4: Add the choice**

In `src/exchange/events.py`, add `COUNTERPARTY_CHOSEN = "COUNTERPARTY_CHOSEN"`.

In `src/exchange/agents/broker.py`:

```python
    def choose(self, matches: list[Match], correlation_id: str) -> Match:
        """Pick a counterparty from the shortlist, and record why.

        The shortlist is ranked by relevance alone. Which of them to actually
        trade with is a judgment — history, reliability, whether a stranger is
        worth a first try — and it belongs to an agent, in prose, in the log.
        A weight here would decide it silently and need a number nobody could
        justify.
        """
        if len(matches) <= 1:
            return matches[0]

        lines = []
        for i, m in enumerate(matches, start=1):
            seller = self._exchange.state().open_orders[m.ask_order_id].actor_id
            recalled = self.subconscious.recall(seller)
            lines.append(
                f"{i}. seller {seller} at {m.clearing_price} per unit, "
                f"{m.qty} units. History: "
                + ("; ".join(recalled) if recalled else "never dealt with them.")
            )

        reply = self._diplomat.act(
            "Choose which of these to trade with. Answer with the number, then one "
            "sentence of reasoning.\n" + "\n".join(lines)
        )
        self._promote(reply)

        index = _first_index(reply, len(matches))
        chosen = matches[index]
        AgentJournal(self._exchange.log, self.actor_id, correlation_id)._append(
            ev.COUNTERPARTY_CHOSEN,
            {"ask_order_id": chosen.ask_order_id, "reason": reply,
             "shortlist": [m.ask_order_id for m in matches]},
        )
        return chosen
```

And a module-level helper:

```python
def _first_index(text: str, count: int) -> int:
    """The first 1-based number in `text` that names a shortlist entry.

    A model that will not answer must not stop the market, so an unparseable
    reply falls back to the most relevant candidate — and the reply is still
    journalled, so the audit trail shows what it said.
    """
    for token in re.findall(r"\d+", text):
        value = int(token)
        if 1 <= value <= count:
            return value - 1
    return 0
```

Import `re` and `from exchange import events as ev`.

- [ ] **Step 5: Fix every `find_candidates` caller**

`find_supply` no longer passes `counterparty_scores`. Run the suite and fix each failure by removing the argument.

- [ ] **Step 6: Run the suite and commit**

```bash
.venv/bin/pytest -q
git add src/exchange/matching.py src/exchange/agents/broker.py src/exchange/events.py tests/
git commit -m "feat: the agent picks the counterparty, and the reason is logged"
```

---

### Task 4: Insight lots and the privacy floor

The privacy floor is a gate, not a promise. It runs before a lot is listed and its verdict is logged, because *"you're selling my campaign to my competitor"* is the first question a judge asks and the answer must be mechanical.

**Files:**
- Create: `src/exchange/house/__init__.py`
- Create: `src/exchange/house/insights.py`
- Modify: `src/exchange/events.py`
- Test: `tests/test_insights.py`

**Interfaces:**
- Consumes: `Asset`, `AssetKind`, `Currency` from Plan 1; `new_id`.
- Produces:
  - `exchange.house.insights.K_MIN` (25)
  - `exchange.house.insights.PrivacyVerdict(allowed: bool, reason: str, k: int)` — frozen
  - `exchange.house.insights.check_privacy(contributor_ids, k_min=K_MIN) -> PrivacyVerdict`
  - `exchange.house.insights.mint_lot(headline, playbook, contributor_ids, category) -> Asset`
  - Event constants `INSIGHT_MINTED`, `PRIVACY_REFUSED`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_insights.py`:

```python
import pytest

from exchange.house.insights import (
    K_MIN,
    check_privacy,
    mint_lot,
)
from exchange.models import AssetKind, Currency

MANY = tuple(f"m_{i}" for i in range(30))


def test_a_lot_from_enough_merchants_is_allowed():
    verdict = check_privacy(MANY)

    assert verdict.allowed is True
    assert verdict.k == 30


def test_a_lot_from_too_few_merchants_is_refused():
    verdict = check_privacy(tuple(f"m_{i}" for i in range(5)))

    assert verdict.allowed is False
    assert str(K_MIN) in verdict.reason


def test_a_single_merchant_lot_is_refused():
    """The case the whole floor exists to prevent."""
    verdict = check_privacy(("m_solo",))

    assert verdict.allowed is False


def test_duplicate_contributors_do_not_inflate_k():
    """Counting rows instead of merchants would let one merchant clear the floor."""
    verdict = check_privacy(("m_a",) * 40)

    assert verdict.k == 1
    assert verdict.allowed is False


def test_the_floor_is_exactly_k_min():
    assert check_privacy(tuple(f"m_{i}" for i in range(K_MIN))).allowed is True
    assert check_privacy(tuple(f"m_{i}" for i in range(K_MIN - 1))).allowed is False


def test_a_minted_lot_is_an_insight_priced_in_credits():
    lot = mint_lot("skincare AOV up 12%", {"channel": "meta"}, MANY, "skincare")

    assert lot.kind == AssetKind.INSIGHT
    assert lot.currency == Currency.CREDITS
    assert lot.origin_actor_id == "house"


def test_a_minted_lot_carries_its_headline_and_k():
    lot = mint_lot("skincare AOV up 12%", {"channel": "meta"}, MANY, "skincare")

    assert lot.spec["headline"] == "skincare AOV up 12%"
    assert lot.spec["k"] == 30
    assert lot.spec["category"] == "skincare"


def test_the_playbook_is_carried_but_is_not_the_headline():
    """The free half creates the demand; the auctioned half is the answer."""
    lot = mint_lot("conversion up 3.2x", {"channel": "meta", "spend": 40000}, MANY, "s")

    assert lot.spec["playbook"]["spend"] == 40000
    assert "spend" not in lot.spec["headline"]


def test_minting_below_the_floor_raises():
    with pytest.raises(ValueError, match="privacy"):
        mint_lot("a headline", {}, ("m_solo",), "skincare")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_insights.py -v`
Expected: FAIL — no module `exchange.house.insights`.

- [ ] **Step 3: Implement**

Create `src/exchange/house/__init__.py` empty, then `src/exchange/house/insights.py`:

```python
"""Market intelligence, and the floor that decides whether it may exist.

Razorpay sees every transaction across every merchant, so it can see what no
single business can. Selling that is a real product — and it is also the
first thing a judge will push on, so the protection is a mechanical check
whose verdict is logged, not a promise in a README.
"""
from __future__ import annotations

from dataclasses import dataclass

from exchange.ids import new_id
from exchange.models import Asset, AssetKind, Currency

K_MIN = 25
HOUSE_ACTOR_ID = "house"


@dataclass(frozen=True)
class PrivacyVerdict:
    allowed: bool
    reason: str
    k: int


def check_privacy(contributor_ids, k_min: int = K_MIN) -> PrivacyVerdict:
    """Decide whether a lot derived from these merchants may be published.

    `k` counts DISTINCT merchants. Counting rows would let one merchant's
    activity clear the floor by appearing repeatedly, which is precisely the
    single-merchant disclosure the floor exists to prevent.
    """
    k = len(set(contributor_ids))
    if k < k_min:
        return PrivacyVerdict(
            False,
            f"derived from {k} merchants, below the floor of {k_min}",
            k,
        )
    return PrivacyVerdict(True, f"derived from {k} merchants", k)


def mint_lot(headline: str, playbook: dict, contributor_ids, category: str) -> Asset:
    """Mint an insight lot, or refuse.

    Only the house mints these, and only INSIGHT assets trade in points —
    both invariants are enforced elsewhere and asserted by the accountant.
    """
    verdict = check_privacy(contributor_ids)
    if not verdict.allowed:
        raise ValueError(f"privacy floor refused this lot: {verdict.reason}")

    return Asset(
        asset_id=new_id("ins"),
        kind=AssetKind.INSIGHT,
        title=headline,
        spec={
            "headline": headline,
            "playbook": playbook,
            "contributor_ids": sorted(set(contributor_ids)),
            "k": verdict.k,
            "category": category,
        },
        currency=Currency.CREDITS,
        origin_actor_id=HOUSE_ACTOR_ID,
    )
```

In `src/exchange/events.py`, add:

```python
INSIGHT_MINTED = "INSIGHT_MINTED"
PRIVACY_REFUSED = "PRIVACY_REFUSED"
```

- [ ] **Step 4: Run tests and commit**

```bash
.venv/bin/pytest tests/test_insights.py -v   # 9 passed
.venv/bin/pytest -q
git add src/exchange/house/ src/exchange/events.py tests/test_insights.py
git commit -m "feat: insight lots behind a mechanical privacy floor"
```

---

### Task 5: The house agent and the victory feed

Mines real settled activity from the event log — that is what makes "only Razorpay could build this" true rather than asserted.

**Files:**
- Create: `src/exchange/house/agent.py`
- Test: `tests/test_house_agent.py`

**Interfaces:**
- Consumes: `EventLog`, `fold`, the insight module, `LLMProvider`, `AgentJournal`.
- Produces: `exchange.house.agent.HouseAgent(log, provider)` with `observe() -> list[dict]`, `mint_from(observations, correlation_id) -> Asset | None`, `feed() -> tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_house_agent.py`:

```python
import pytest

from exchange.eventlog import EventLog
from exchange.events import SETTLEMENT_COMPLETED, SETTLEMENT_INITIATED
from exchange.house.agent import HouseAgent
from exchange.llm.scripted import ScriptedProvider


@pytest.fixture
def log(tmp_path):
    lg = EventLog(str(tmp_path / "house.db"))
    yield lg
    lg.close()


def _settle(log, actor, amount, corr):
    log.append(actor, SETTLEMENT_INITIATED,
               {"settlement_id": f"stl_{corr}", "match_id": f"m_{corr}",
                "currency": "INR", "amount": amount}, correlation_id=corr)
    log.append(actor, SETTLEMENT_COMPLETED,
               {"settlement_id": f"stl_{corr}", "razorpay_payment_id": "pay"},
               correlation_id=corr)


def test_observe_reads_settled_activity_from_the_log(log):
    for i in range(3):
        _settle(log, f"m_{i}", 100_000 * (i + 1), f"c{i}")

    observations = HouseAgent(log, ScriptedProvider([])).observe()

    assert len(observations) == 3
    assert {o["actor_id"] for o in observations} == {"m_0", "m_1", "m_2"}


def test_observe_ignores_settlements_that_never_completed(log):
    """A PENDING settlement is not evidence of anything yet."""
    log.append("m_a", SETTLEMENT_INITIATED,
               {"settlement_id": "stl_1", "match_id": "m1",
                "currency": "INR", "amount": 500}, correlation_id="c")

    assert HouseAgent(log, ScriptedProvider([])).observe() == []


def test_minting_needs_enough_distinct_merchants(log):
    for i in range(30):
        _settle(log, f"m_{i}", 100_000, f"c{i}")
    house = HouseAgent(log, ScriptedProvider(["skincare demand is up 12% week on week"]))

    lot = house.mint_from(house.observe(), "c_house")

    assert lot is not None
    assert lot.spec["k"] == 30


def test_minting_refuses_below_the_floor_and_logs_it(log):
    for i in range(4):
        _settle(log, f"m_{i}", 100_000, f"c{i}")
    house = HouseAgent(log, ScriptedProvider(["a headline"]))

    lot = house.mint_from(house.observe(), "c_house")

    assert lot is None
    types = [e.type for e in log.read_by_correlation("c_house")]
    assert "PRIVACY_REFUSED" in types
    assert "INSIGHT_MINTED" not in types


def test_a_refusal_records_how_many_merchants_it_had(log):
    for i in range(4):
        _settle(log, f"m_{i}", 100_000, f"c{i}")
    house = HouseAgent(log, ScriptedProvider(["a headline"]))

    house.mint_from(house.observe(), "c_house")

    refused = [e for e in log.read_by_correlation("c_house")
               if e.type == "PRIVACY_REFUSED"][0]
    assert refused.payload["k"] == 4


def test_minting_writes_the_lot_to_the_log(log):
    for i in range(30):
        _settle(log, f"m_{i}", 100_000, f"c{i}")
    house = HouseAgent(log, ScriptedProvider(["skincare demand is up"]))

    house.mint_from(house.observe(), "c_house")

    minted = [e for e in log.read_by_correlation("c_house")
              if e.type == "INSIGHT_MINTED"][0]
    assert minted.payload["headline"] == "skincare demand is up"


def test_the_feed_carries_headlines_and_never_playbooks(log):
    """The free half creates the hunger; the auction sells the answer."""
    for i in range(30):
        _settle(log, f"m_{i}", 100_000, f"c{i}")
    house = HouseAgent(log, ScriptedProvider(["skincare demand is up"]))
    house.mint_from(house.observe(), "c_house")

    feed = house.feed()

    assert feed == ("skincare demand is up",)


def test_the_house_never_bids():
    """It mints, publishes and clears. A house that buys is not a market."""
    assert not hasattr(HouseAgent, "bid")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_house_agent.py -v`
Expected: FAIL — no module `exchange.house.agent`.

- [ ] **Step 3: Implement**

Create `src/exchange/house/agent.py`:

```python
"""Razorpay's own agent. It mints, publishes and clears; it never buys.

It reads REAL settled activity out of the event log rather than a seeded
corpus, because that is what makes the claim true: this product exists only
for whoever is processing the payments.
"""
from __future__ import annotations

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.insights import HOUSE_ACTOR_ID, check_privacy, mint_lot
from exchange.llm.base import LLMMessage, LLMProvider

HEADLINE_PROMPT = """You are Razorpay's market research agent. You see settled
transactions across many merchants and write one headline naming a pattern worth
paying for.

Write ONE sentence. Name the category and the direction. Never name a merchant,
never quote a single transaction, and never include a figure that could identify
one business."""


class HouseAgent:
    def __init__(self, log: EventLog, provider: LLMProvider) -> None:
        self._log = log
        self._provider = provider
        self._lots: list = []

    def observe(self) -> list[dict]:
        """Settled INR activity, one row per completed settlement.

        Only COMPLETED counts. A PENDING settlement is a payment nobody has
        made yet, and treating it as evidence would let unpaid intent become
        market intelligence.
        """
        events = self._log.read_all()
        initiated = {
            e.payload["settlement_id"]: e
            for e in events
            if e.type == ev.SETTLEMENT_INITIATED
        }
        out = []
        for event in events:
            if event.type != ev.SETTLEMENT_COMPLETED:
                continue
            opened = initiated.get(event.payload["settlement_id"])
            if opened is None:
                continue
            out.append({
                "actor_id": opened.actor_id,
                "amount": opened.payload.get("amount", 0),
                "currency": opened.payload.get("currency", "INR"),
            })
        return out

    def mint_from(self, observations: list[dict], correlation_id: str):
        """Turn observations into a lot, or refuse and say why.

        The refusal is logged as loudly as the success — a floor nobody can
        see is indistinguishable from no floor.
        """
        contributors = [o["actor_id"] for o in observations]
        verdict = check_privacy(contributors)
        if not verdict.allowed:
            self._log.append(
                HOUSE_ACTOR_ID,
                ev.PRIVACY_REFUSED,
                {"reason": verdict.reason, "k": verdict.k},
                correlation_id=correlation_id,
            )
            return None

        total = sum(o["amount"] for o in observations)
        response = self._provider.complete(
            [LLMMessage(
                "user",
                f"{len(observations)} settled trades across {verdict.k} merchants, "
                f"totalling {total} paise. Write the headline.",
            )],
            system=HEADLINE_PROMPT,
            max_tokens=120,
        )
        headline = response.text.strip()

        lot = mint_lot(
            headline=headline,
            playbook={"observed_trades": len(observations), "total_paise": total},
            contributor_ids=contributors,
            category="market",
        )
        self._lots.append(lot)
        self._log.append(
            HOUSE_ACTOR_ID,
            ev.INSIGHT_MINTED,
            {"asset_id": lot.asset_id, "headline": headline, "k": verdict.k,
             "contributor_ids": lot.spec["contributor_ids"]},
            correlation_id=correlation_id,
        )
        return lot

    def feed(self) -> tuple[str, ...]:
        """The free half: headlines only, never a playbook."""
        return tuple(lot.spec["headline"] for lot in self._lots)
```

- [ ] **Step 4: Run tests and commit**

```bash
.venv/bin/pytest tests/test_house_agent.py -v   # 8 passed
.venv/bin/pytest -q
git add src/exchange/house/agent.py tests/test_house_agent.py
git commit -m "feat: house agent mines settled activity into a victory feed"
```

---

### Task 6: Sealed-bid second-price auction

**Files:**
- Create: `src/exchange/house/auction.py`
- Modify: `src/exchange/events.py`
- Test: `tests/test_auction.py`

**Interfaces:**
- Produces:
  - `exchange.house.auction.Bid(actor_id: str, amount: int, reason: str)` — frozen
  - `exchange.house.auction.Clearing(winner_id: str | None, price: int | None, reason: str)` — frozen
  - `exchange.house.auction.clear(bids: list[Bid]) -> Clearing`
  - `exchange.house.auction.run_auction(log, asset_id, bids, correlation_id) -> Clearing`
  - Event constants `AUCTION_OPENED`, `BID_PLACED`, `AUCTION_CLEARED`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_auction.py`:

```python
import pytest

from exchange.eventlog import EventLog
from exchange.house.auction import Bid, clear, run_auction


@pytest.fixture
def log(tmp_path):
    lg = EventLog(str(tmp_path / "auction.db"))
    yield lg
    lg.close()


def test_the_highest_bidder_wins():
    result = clear([Bid("m_a", 800, ""), Bid("m_b", 1850, ""), Bid("m_c", 1200, "")])

    assert result.winner_id == "m_b"


def test_the_winner_pays_the_second_price():
    """The whole point: your bid decides whether you win, not what you pay."""
    result = clear([Bid("m_a", 800, ""), Bid("m_b", 1850, ""), Bid("m_c", 1200, "")])

    assert result.price == 1200


def test_a_single_bid_does_not_clear():
    """A market of one has no price."""
    result = clear([Bid("m_a", 800, "")])

    assert result.winner_id is None
    assert "one bid" in result.reason


def test_no_bids_does_not_clear():
    result = clear([])

    assert result.winner_id is None


def test_a_tie_at_the_top_clears_at_that_price():
    result = clear([Bid("m_a", 1000, ""), Bid("m_b", 1000, "")])

    assert result.price == 1000
    assert result.winner_id in {"m_a", "m_b"}


def test_running_an_auction_logs_open_bids_and_clearing(log):
    run_auction(log, "ins_1",
                [Bid("m_a", 800, "small category for us"),
                 Bid("m_b", 1850, "we spend 40k a month here")],
                correlation_id="c1")

    types = [e.type for e in log.read_by_correlation("c1")]
    assert types == ["AUCTION_OPENED", "BID_PLACED", "BID_PLACED", "AUCTION_CLEARED"]


def test_each_bid_records_the_reasoning_behind_it(log):
    """Under second-price, honest valuation is optimal — so the reasoning is
    about worth, and that is what belongs in the trail."""
    run_auction(log, "ins_1",
                [Bid("m_a", 800, "small category for us"),
                 Bid("m_b", 1850, "we spend 40k a month here")],
                correlation_id="c1")

    placed = [e for e in log.read_by_correlation("c1") if e.type == "BID_PLACED"]
    assert "40k a month" in placed[1].payload["reason"]


def test_the_clearing_event_records_winner_and_price(log):
    run_auction(log, "ins_1",
                [Bid("m_a", 800, ""), Bid("m_b", 1850, ""), Bid("m_c", 1200, "")],
                correlation_id="c1")

    cleared = [e for e in log.read_by_correlation("c1")
               if e.type == "AUCTION_CLEARED"][0]
    assert cleared.payload["winner_id"] == "m_b"
    assert cleared.payload["price"] == 1200


def test_a_no_clear_is_still_logged(log):
    """Not clearing is an outcome, not an error."""
    run_auction(log, "ins_1", [Bid("m_a", 800, "")], correlation_id="c1")

    cleared = [e for e in log.read_by_correlation("c1")
               if e.type == "AUCTION_CLEARED"][0]
    assert cleared.payload["winner_id"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_auction.py -v`
Expected: FAIL — no module `exchange.house.auction`.

- [ ] **Step 3: Implement**

Create `src/exchange/house/auction.py`:

```python
"""Sealed-bid, second-price. Highest bidder wins and pays the runner-up's bid.

Why second-price: under first-price no agent ever bids what a lot is worth to
it — it shades down, and how far depends on guessing rivals, so the reasoning
in the log becomes mind-reading rather than valuation. Here a bid decides
WHETHER you win but not WHAT you pay, so bidding true value is optimal and the
sentence that lands in the audit trail is about worth.
"""
from __future__ import annotations

from dataclasses import dataclass

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.insights import HOUSE_ACTOR_ID


@dataclass(frozen=True)
class Bid:
    actor_id: str
    amount: int
    reason: str


@dataclass(frozen=True)
class Clearing:
    winner_id: str | None
    price: int | None
    reason: str


def clear(bids: list[Bid]) -> Clearing:
    """Resolve a sealed auction.

    Fewer than two bids does not clear, and that is correct rather than an
    error: second-price needs a second price, and a market of one has none.
    """
    if len(bids) < 2:
        return Clearing(None, None, f"only {len(bids)} bid(s); a market of one has no price")

    ranked = sorted(bids, key=lambda b: b.amount, reverse=True)
    return Clearing(ranked[0].actor_id, ranked[1].amount, "cleared at the second price")


def run_auction(
    log: EventLog,
    asset_id: str,
    bids: list[Bid],
    correlation_id: str,
) -> Clearing:
    """Open, record every bid with its reasoning, and clear — all on one id."""
    log.append(HOUSE_ACTOR_ID, ev.AUCTION_OPENED,
               {"asset_id": asset_id}, correlation_id=correlation_id)

    for bid in bids:
        log.append(bid.actor_id, ev.BID_PLACED,
                   {"asset_id": asset_id, "amount": bid.amount, "reason": bid.reason},
                   correlation_id=correlation_id)

    result = clear(bids)
    log.append(HOUSE_ACTOR_ID, ev.AUCTION_CLEARED,
               {"asset_id": asset_id, "winner_id": result.winner_id,
                "price": result.price, "reason": result.reason},
               correlation_id=correlation_id)
    return result
```

In `src/exchange/events.py`, add:

```python
AUCTION_OPENED = "AUCTION_OPENED"
BID_PLACED = "BID_PLACED"
AUCTION_CLEARED = "AUCTION_CLEARED"
```

- [ ] **Step 4: Run tests and commit**

```bash
.venv/bin/pytest tests/test_auction.py -v   # 9 passed
.venv/bin/pytest -q
git add src/exchange/house/auction.py src/exchange/events.py tests/test_auction.py
git commit -m "feat: sealed second-price auction, reasoning recorded per bid"
```

---

### Task 7: The Scout values a lot

No formula prices an insight. The Scout judges what it is worth to *this* merchant and says why — and the bid is still bounded by the gate.

**Files:**
- Modify: `src/exchange/agents/broker.py`
- Test: `tests/test_broker.py`

**Interfaces:**
- Produces: `Broker.value_insight(headline: str, category: str, cap: int) -> Bid`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_broker.py`:

```python
def test_the_scout_values_a_lot_and_says_why(exchange):
    broker = Broker("m_buyer", exchange,
                    ScriptedProvider(["BID: 1850 we spend 40k a month in this category"]))

    bid = broker.value_insight("skincare AOV up 12%", "skincare", cap=50_000)

    assert bid.amount == 1850
    assert "40k a month" in bid.reason
    assert bid.actor_id == "m_buyer"


def test_a_valuation_above_the_cap_is_clamped_not_refused(exchange):
    """Judgment picks the number; the cap decides what is allowed. An agent
    that wants more than it may spend still bids — at the limit."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["BID: 999999 worth everything"]))

    bid = broker.value_insight("skincare AOV up 12%", "skincare", cap=50_000)

    assert bid.amount == 50_000


def test_an_unparseable_valuation_bids_nothing(exchange):
    """Silence is not a bid. Guessing one would put points at risk on noise."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["I am not sure about this"]))

    bid = broker.value_insight("skincare AOV up 12%", "skincare", cap=50_000)

    assert bid.amount == 0


def test_the_headline_and_recall_both_reach_the_scout(exchange):
    provider = ScriptedProvider(["BEHAVIOURAL: past lots in this category paid off",
                                 "BID: 900 worth a look"])
    broker = Broker("m_buyer", exchange, provider)
    from exchange.agents.context import ContextState
    broker.subconscious.consolidate(ContextState(facts=("x",)), "house", "skincare")

    broker.value_insight("skincare AOV up 12%", "skincare", cap=50_000)

    sent = provider.calls[-1]["messages"][0].content
    assert "skincare AOV up 12%" in sent
    assert "past lots in this category paid off" in sent
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_broker.py -k value_insight -v` and `-k "values_a_lot or clamped or unparseable_valuation or headline_and_recall"`
Expected: FAIL — `value_insight` undefined.

- [ ] **Step 3: Implement**

In `src/exchange/agents/broker.py`:

```python
    def value_insight(self, headline: str, category: str, cap: int) -> Bid:
        """Ask the Scout what this lot is worth to us, and bound the answer.

        No formula prices an insight — what a headline is worth depends on
        this merchant's position, which is a judgment. Under second-price
        the honest number is also the optimal one, so the reasoning that
        lands in the log is about worth rather than about rivals.

        The cap is not a suggestion. An agent that wants to spend more than
        it may still bids, at the limit — judgment picks the number, the
        bound decides what is allowed.
        """
        recalled = self.subconscious.recall("house", category=category)
        reply = self._scout.act(
            f"A market intelligence lot is up for auction:\n\n  {headline}\n\n"
            f"Category: {category}. You may bid at most {cap} points.\n"
            "Answer with 'BID: <integer>' then one sentence on why it is worth "
            "that to us. Bid what it is actually worth — you pay the runner-up's "
            "price, not your own.",
            facts=recalled,
        )
        self._promote(reply)

        match = re.search(r"BID:\s*(\d+)", reply, re.IGNORECASE)
        amount = min(int(match.group(1)), cap) if match else 0
        return Bid(actor_id=self.actor_id, amount=amount, reason=reply)
```

Import `from exchange.house.auction import Bid`.

- [ ] **Step 4: Run the suite and commit**

```bash
.venv/bin/pytest -q
git add src/exchange/agents/broker.py tests/test_broker.py
git commit -m "feat: the Scout judges what an insight is worth, bounded by the cap"
```

---

### Task 8: The points ledger and earnings

Minted only by the accountant. Rewards broker skill, not size.

**Files:**
- Create: `src/exchange/house/points.py`
- Test: `tests/test_points.py`

**Interfaces:**
- Produces:
  - `exchange.house.points.EARNING_RATE_BPS`, `ROYALTY_SHARE_BPS`
  - `exchange.house.points.points_for_settlement(amount, ask_price, qty, delivered) -> int`
  - `exchange.house.points.royalty_for(clearing_price, contributor_count) -> int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_points.py`:

```python
from exchange.house.points import points_for_settlement, royalty_for


def test_negotiating_below_the_ask_earns_more_than_paying_it():
    """Skill, not size: the whole point of the earning rule."""
    sharp = points_for_settlement(amount=380_000, ask_price=1940, qty=200, delivered=True)
    full = points_for_settlement(amount=388_000, ask_price=1940, qty=200, delivered=True)

    assert sharp > full


def test_a_small_sharp_trade_can_out_earn_a_large_sloppy_one():
    """Volume-weighting would make the biggest merchant win by round three."""
    small_sharp = points_for_settlement(190_000, ask_price=1940, qty=100, delivered=True)
    big_sloppy = points_for_settlement(1_940_000, ask_price=1940, qty=1000, delivered=True)

    assert small_sharp > big_sloppy


def test_paying_the_full_ask_still_earns_something():
    assert points_for_settlement(388_000, ask_price=1940, qty=200, delivered=True) > 0


def test_paying_above_the_ask_earns_nothing():
    """You cannot be paid for overpaying."""
    assert points_for_settlement(500_000, ask_price=1940, qty=200, delivered=True) == 0


def test_an_undelivered_trade_earns_nothing():
    assert points_for_settlement(380_000, ask_price=1940, qty=200, delivered=False) == 0


def test_points_are_whole_numbers():
    assert isinstance(points_for_settlement(380_001, 1940, 200, True), int)


def test_a_royalty_is_split_across_contributors():
    assert royalty_for(clearing_price=1200, contributor_count=30) < 1200


def test_a_royalty_scales_with_the_clearing_price():
    """A win that sells for more earns its contributors more."""
    assert royalty_for(2400, 30) > royalty_for(1200, 30)


def test_a_royalty_never_exceeds_the_clearing_price():
    assert royalty_for(1200, 1) <= 1200
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_points.py -v`
Expected: FAIL — no module `exchange.house.points`.

- [ ] **Step 3: Implement**

Create `src/exchange/house/points.py`:

```python
"""What a merchant earns, and why it is not volume.

Volume-weighting makes the largest merchant win by round three: it earns
most, buys the best intelligence, trades better, earns more. The market
ossifies and there is nothing left to watch. So the rule pays for MARGIN
CAPTURED — the gap between the ask and what was actually paid — which a
small merchant can win by negotiating well and a large one can lose by
overpaying.

Minted only by the accountant. Nothing else may mint.
"""
from __future__ import annotations

EARNING_RATE_BPS = 500      # 5% of margin captured, in basis points
BASE_POINTS = 10            # a completed trade is worth something on its own
ROYALTY_SHARE_BPS = 3000    # 30% of a clearing price goes back to contributors


def points_for_settlement(
    amount: int,
    ask_price: int,
    qty: int,
    delivered: bool,
) -> int:
    """Points for one settled trade.

    `amount` is what was actually paid for the whole lot; `ask_price * qty` is
    what the seller opened at. The difference is the margin the broker captured
    by negotiating, and that is what is rewarded.
    """
    if not delivered:
        return 0

    asked = ask_price * qty
    margin = asked - amount
    if margin < 0:
        return 0  # you cannot be paid for overpaying

    return BASE_POINTS + (margin * EARNING_RATE_BPS) // 10_000


def royalty_for(clearing_price: int, contributor_count: int) -> int:
    """What each contributing merchant earns when a lot derived from their
    activity is bought.

    Priced off the clearing price so a valuable win earns its contributors
    more. This is what turns the intelligence product from extraction into a
    deal — and it is the answer to "you're selling my campaign to my
    competitor".
    """
    if contributor_count <= 0:
        return 0
    pool = (clearing_price * ROYALTY_SHARE_BPS) // 10_000
    return pool // contributor_count
```

- [ ] **Step 4: Run tests and commit**

```bash
.venv/bin/pytest tests/test_points.py -v   # 9 passed
.venv/bin/pytest -q
git add src/exchange/house/points.py tests/test_points.py
git commit -m "feat: points reward margin captured, not volume"
```

---

### Task 9: The accountant — reconcile and assert

Exchange-level, because reconciliation needs both sides of every trade and point conservation is a global invariant.

**Files:**
- Create: `src/exchange/house/accountant.py`
- Modify: `src/exchange/events.py`
- Test: `tests/test_accountant.py`

**Interfaces:**
- Produces:
  - `exchange.house.accountant.Drift(settlement_id, local_status, remote_status)` — frozen
  - `exchange.house.accountant.Violation(kind: str, detail: str)` — frozen
  - `exchange.house.accountant.Accountant(log, client)` with `reconcile() -> list[Drift]` and `assert_invariants() -> list[Violation]`
  - Event constants `RECONCILED`, `DRIFT_DETECTED`, `INVARIANT_VIOLATED`, `POINTS_MINTED`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_accountant.py`:

```python
import pytest

from exchange.eventlog import EventLog
from exchange.events import (
    CREDITS_TRANSFERRED,
    MATCH_PROPOSED,
    POLICY_DECIDED,
    SETTLEMENT_COMPLETED,
    SETTLEMENT_INITIATED,
)
from exchange.house.accountant import Accountant
from tests.test_rails import FakeRazorpay


@pytest.fixture
def log(tmp_path):
    lg = EventLog(str(tmp_path / "acct.db"))
    yield lg
    lg.close()


def _initiated(log, sid, order_id, corr="c"):
    log.append("m_a", SETTLEMENT_INITIATED,
               {"settlement_id": sid, "match_id": "mch", "currency": "INR",
                "amount": 970_000, "razorpay_order_id": order_id},
               correlation_id=corr)


def test_a_settlement_captured_upstream_but_pending_locally_is_drift(log):
    """The dropped webhook. The whole failure demo rests on catching this."""
    _initiated(log, "stl_1", "order_1")
    client = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_1", "status": "captured"}]}
    })

    drifts = Accountant(log, client).reconcile()

    assert len(drifts) == 1
    assert drifts[0].local_status == "PENDING"
    assert drifts[0].remote_status == "captured"


def test_a_settlement_pending_on_both_sides_is_not_drift(log):
    _initiated(log, "stl_1", "order_1")

    assert Accountant(log, FakeRazorpay(payments_by_order={})).reconcile() == []


def test_a_completed_settlement_with_a_captured_payment_is_not_drift(log):
    _initiated(log, "stl_1", "order_1")
    log.append("m_a", SETTLEMENT_COMPLETED,
               {"settlement_id": "stl_1", "razorpay_payment_id": "pay_1"},
               correlation_id="c")
    client = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_1", "status": "captured"}]}
    })

    assert Accountant(log, client).reconcile() == []


def test_reconciling_logs_what_it_checked(log):
    _initiated(log, "stl_1", "order_1")

    Accountant(log, FakeRazorpay(payments_by_order={})).reconcile()

    assert any(e.type == "RECONCILED" for e in log.read_all())


def test_drift_is_logged_with_both_sides(log):
    _initiated(log, "stl_1", "order_1")
    client = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_1", "status": "captured"}]}
    })

    Accountant(log, client).reconcile()

    drift = [e for e in log.read_all() if e.type == "DRIFT_DETECTED"][0]
    assert drift.payload["local_status"] == "PENDING"
    assert drift.payload["remote_status"] == "captured"


def test_points_that_appear_from_nowhere_are_a_violation(log):
    """Only the accountant mints. A transfer from an actor that never
    received any is points conjured out of nothing."""
    log.append("m_a", CREDITS_TRANSFERRED,
               {"from_actor_id": "m_a", "to_actor_id": "m_b", "amount": 500},
               correlation_id="c")

    violations = Accountant(log, FakeRazorpay()).assert_invariants()

    assert any(v.kind == "points_not_conserved" for v in violations)


def test_a_settlement_without_a_preceding_allow_is_a_violation(log):
    _initiated(log, "stl_1", "order_1")

    violations = Accountant(log, FakeRazorpay()).assert_invariants()

    assert any(v.kind == "ungated_settlement" for v in violations)


def test_a_denied_match_is_not_an_orphan(log):
    """MATCH_PROPOSED precedes the gate by design, so a denied match is in
    the log legitimately. Joining on presence would flag every refusal."""
    log.append("m_a", MATCH_PROPOSED,
               {"match_id": "mch_1", "clearing_price": 1940, "qty": 200},
               correlation_id="c")
    log.append("m_a", POLICY_DECIDED,
               {"decision_id": "d1", "action_ref": "mch_1", "verdict": "DENY",
                "reason": "capped", "limits_evaluated": {}, "ts": "t"},
               correlation_id="c")

    violations = Accountant(log, FakeRazorpay()).assert_invariants()

    assert not any(v.kind == "orphaned_match" for v in violations)


def test_a_match_that_never_reached_the_gate_is_an_orphan(log):
    log.append("m_a", MATCH_PROPOSED,
               {"match_id": "mch_1", "clearing_price": 1940, "qty": 200},
               correlation_id="c")

    violations = Accountant(log, FakeRazorpay()).assert_invariants()

    assert any(v.kind == "orphaned_match" for v in violations)


def test_a_stale_projection_cache_is_a_violation(log):
    """The incremental projection's correctness rests on this check existing.
    Without it, a cache that silently lagged the log would never be caught."""
    log.append("m_a", MATCH_PROPOSED,
               {"match_id": "mch_1", "clearing_price": 1940, "qty": 200},
               correlation_id="c")

    class StaleExchange:
        def state(self):
            from exchange.projections import ExchangeState
            return ExchangeState()  # empty: pretends the log is empty

    violations = Accountant(log, FakeRazorpay(),
                            exchange=StaleExchange()).assert_invariants()

    assert any(v.kind == "projection_drift" for v in violations)


def test_a_matching_projection_is_not_a_violation(log):
    log.append("m_a", MATCH_PROPOSED,
               {"match_id": "mch_1", "clearing_price": 1940, "qty": 200},
               correlation_id="c")

    class FreshExchange:
        def __init__(self, lg):
            self._lg = lg

        def state(self):
            from exchange.projections import fold
            return fold(self._lg.read_all())

    violations = Accountant(log, FakeRazorpay(),
                            exchange=FreshExchange(log)).assert_invariants()

    assert not any(v.kind == "projection_drift" for v in violations)


def test_a_clean_log_has_no_violations(log):
    log.append("m_a", MATCH_PROPOSED,
               {"match_id": "mch_1", "clearing_price": 1940, "qty": 200},
               correlation_id="c")
    log.append("m_a", POLICY_DECIDED,
               {"decision_id": "d1", "action_ref": "mch_1", "verdict": "ALLOW",
                "reason": "ok", "limits_evaluated": {}, "ts": "t"},
               correlation_id="c")
    _initiated(log, "stl_1", "order_1")

    assert Accountant(log, FakeRazorpay()).assert_invariants() == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_accountant.py -v`
Expected: FAIL — no module `exchange.house.accountant`.

- [ ] **Step 3: Implement**

Create `src/exchange/house/accountant.py`:

```python
"""The books, and whether they are honest.

Exchange-level rather than per-merchant: reconciliation needs both sides of
every trade, and point conservation is a global invariant that a per-merchant
accountant would only ever see half of.

Its reconciliation against Razorpay is also the DELIVERY SIGNAL the memory
loop has been missing — a settlement that completes cleanly is evidence of
reliability, one that drifts is evidence against.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.insights import HOUSE_ACTOR_ID

ACCOUNTANT_ACTOR_ID = "accountant"


@dataclass(frozen=True)
class Drift:
    settlement_id: str
    local_status: str
    remote_status: str


@dataclass(frozen=True)
class Violation:
    kind: str
    detail: str


class Accountant:
    def __init__(self, log: EventLog, client, exchange=None) -> None:
        self._log = log
        self._client = client
        # Optional: when given, the accountant checks that the Exchange's
        # incremental projection still agrees with a full fold. That check is
        # the only thing standing between a fast cache and a second source of
        # truth, so the cache is only safe BECAUSE this exists.
        self._exchange = exchange

    def reconcile(self) -> list[Drift]:
        """Compare local settlement records against Razorpay's own state.

        Catches the dropped webhook: captured upstream, still PENDING here.
        That mismatch is what the failure demo turns on, and it is a far
        better thing to show than a declined card.
        """
        events = self._log.read_all()
        completed = {
            e.payload["settlement_id"]
            for e in events if e.type == ev.SETTLEMENT_COMPLETED
        }

        drifts: list[Drift] = []
        checked = 0
        for event in events:
            if event.type != ev.SETTLEMENT_INITIATED:
                continue
            order_id = event.payload.get("razorpay_order_id")
            if not order_id:
                continue
            checked += 1
            sid = event.payload["settlement_id"]
            local = "COMPLETED" if sid in completed else "PENDING"

            payments = self._client.order.payments(order_id)
            remote = "none"
            for item in payments.get("items", []):
                if item.get("status") == "captured":
                    remote = "captured"
                    break

            if local == "PENDING" and remote == "captured":
                drift = Drift(sid, local, remote)
                drifts.append(drift)
                self._log.append(
                    ACCOUNTANT_ACTOR_ID, ev.DRIFT_DETECTED,
                    {"settlement_id": sid, "local_status": local,
                     "remote_status": remote, "razorpay_order_id": order_id},
                    correlation_id=f"recon_{sid}",
                )

        self._log.append(
            ACCOUNTANT_ACTOR_ID, ev.RECONCILED,
            {"settlements_checked": checked, "drifts": len(drifts)},
            correlation_id="recon",
        )
        return drifts

    def assert_invariants(self) -> list[Violation]:
        """Everything that must be true of the log, checked against the log."""
        events = self._log.read_all()
        violations: list[Violation] = []

        # Points are conserved and minted only here. A negative balance means
        # an actor spent points it was never given.
        balances: dict[str, int] = defaultdict(int)
        for e in events:
            if e.type == ev.CREDITS_TRANSFERRED:
                balances[e.payload["from_actor_id"]] -= e.payload["amount"]
                balances[e.payload["to_actor_id"]] += e.payload["amount"]
        for actor, balance in balances.items():
            if balance < 0 and actor not in (HOUSE_ACTOR_ID, ACCOUNTANT_ACTOR_ID):
                violations.append(Violation(
                    "points_not_conserved",
                    f"{actor} holds {balance}; only the accountant may mint",
                ))

        # A settlement must have been permitted first.
        allowed = {
            e.payload["action_ref"]
            for e in events
            if e.type == ev.POLICY_DECIDED and e.payload.get("verdict") == "ALLOW"
        }
        decided = {
            e.payload["action_ref"] for e in events if e.type == ev.POLICY_DECIDED
        }
        for e in events:
            if e.type != ev.SETTLEMENT_INITIATED:
                continue
            if e.payload.get("match_id") not in allowed:
                violations.append(Violation(
                    "ungated_settlement",
                    f"settlement {e.payload['settlement_id']} has no preceding ALLOW",
                ))

        # A match must have reached the gate. Join on POLICY_DECIDED, not on
        # presence: MATCH_PROPOSED precedes the gate by design, so a DENIED
        # match is in the log legitimately and must not be flagged.
        for e in events:
            if e.type != ev.MATCH_PROPOSED:
                continue
            if e.payload.get("match_id") not in decided:
                violations.append(Violation(
                    "orphaned_match",
                    f"match {e.payload.get('match_id')} never reached the gate",
                ))

        # The incremental projection must still agree with the authority.
        if self._exchange is not None:
            from exchange.projections import fold

            if self._exchange.state() != fold(events):
                violations.append(Violation(
                    "projection_drift",
                    "the cached projection disagrees with a full fold of the log",
                ))

        if violations:
            for v in violations:
                self._log.append(
                    ACCOUNTANT_ACTOR_ID, ev.INVARIANT_VIOLATED,
                    {"kind": v.kind, "detail": v.detail},
                    correlation_id="invariants",
                )
        return violations
```

In `src/exchange/events.py`, add:

```python
RECONCILED = "RECONCILED"
DRIFT_DETECTED = "DRIFT_DETECTED"
INVARIANT_VIOLATED = "INVARIANT_VIOLATED"
POINTS_MINTED = "POINTS_MINTED"
ACTOR_FROZEN = "ACTOR_FROZEN"
ACTOR_RESUMED = "ACTOR_RESUMED"
```

- [ ] **Step 4: Run tests and commit**

```bash
.venv/bin/pytest tests/test_accountant.py -v   # 12 passed
.venv/bin/pytest -q
git add src/exchange/house/accountant.py src/exchange/events.py tests/test_accountant.py
git commit -m "feat: accountant reconciles against Razorpay and asserts the invariants"
```

---

### Task 10: Freeze, repair, resume — the failure demo

**Files:**
- Modify: `src/exchange/house/accountant.py`
- Modify: `src/exchange/projections.py`
- Test: `tests/test_accountant.py`

**Interfaces:**
- Produces: `Accountant.freeze(actor_id, reason)`, `Accountant.repair(drift)`, `Accountant.resume(actor_id)`; `fold` handles `ACTOR_FROZEN` / `ACTOR_RESUMED`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_accountant.py`:

```python
def test_freezing_an_actor_stops_it_trading(log):
    from exchange.models import ActorStatus
    from exchange.projections import fold

    log.append("m_a", "ACTOR_REGISTERED",
               {"actor_id": "m_a", "kind": "MERCHANT"}, correlation_id="reg")
    Accountant(log, FakeRazorpay()).freeze("m_a", "books disagree")

    assert fold(log.read_all()).actors["m_a"].status == ActorStatus.FROZEN


def test_repairing_a_drift_completes_the_settlement_from_the_remote_truth(log):
    _initiated(log, "stl_1", "order_1")
    client = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_1", "status": "captured"}]}
    })
    accountant = Accountant(log, client)
    drift = accountant.reconcile()[0]

    accountant.repair(drift)

    assert accountant.reconcile() == [], "the drift must be gone after repair"


def test_repair_records_the_payment_id_it_recovered(log):
    _initiated(log, "stl_1", "order_1")
    client = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_recovered", "status": "captured"}]}
    })
    accountant = Accountant(log, client)
    accountant.repair(accountant.reconcile()[0])

    completed = [e for e in log.read_all() if e.type == "SETTLEMENT_COMPLETED"][0]
    assert completed.payload["razorpay_payment_id"] == "pay_recovered"


def test_the_whole_failure_path_is_readable_from_the_log(log):
    """Freeze, repair, resume — the forty-five seconds of the video."""
    log.append("m_a", "ACTOR_REGISTERED",
               {"actor_id": "m_a", "kind": "MERCHANT"}, correlation_id="reg")
    _initiated(log, "stl_1", "order_1")
    client = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_1", "status": "captured"}]}
    })
    accountant = Accountant(log, client)

    drift = accountant.reconcile()[0]
    accountant.freeze("m_a", f"drift on {drift.settlement_id}")
    accountant.repair(drift)
    accountant.resume("m_a")

    types = [e.type for e in log.read_all()]
    for expected in ("DRIFT_DETECTED", "ACTOR_FROZEN",
                     "SETTLEMENT_COMPLETED", "ACTOR_RESUMED"):
        assert expected in types, expected
    assert types.index("ACTOR_FROZEN") < types.index("ACTOR_RESUMED")


def test_a_resumed_actor_can_trade_again(log):
    from exchange.models import ActorStatus
    from exchange.projections import fold

    log.append("m_a", "ACTOR_REGISTERED",
               {"actor_id": "m_a", "kind": "MERCHANT"}, correlation_id="reg")
    accountant = Accountant(log, FakeRazorpay())
    accountant.freeze("m_a", "books disagree")
    accountant.resume("m_a")

    assert fold(log.read_all()).actors["m_a"].status == ActorStatus.ACTIVE
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_accountant.py -k "freez or repair or failure_path or resumed" -v`
Expected: FAIL — methods undefined and the fold ignores the events.

- [ ] **Step 3: Implement the three methods**

Append to `Accountant`:

```python
    def freeze(self, actor_id: str, reason: str) -> None:
        """Stop an actor trading until its books agree again.

        Per-actor, never global: one merchant's drift must not stop the market.
        The policy gate already denies a FROZEN actor, so this is enforcement,
        not advice.
        """
        self._log.append(
            ACCOUNTANT_ACTOR_ID, ev.ACTOR_FROZEN,
            {"actor_id": actor_id, "reason": reason},
            correlation_id=f"freeze_{actor_id}",
        )

    def repair(self, drift: Drift) -> None:
        """Make the local record agree with Razorpay's.

        The remote is the authority for whether money moved — we did not take
        the payment, they did. Repair means recording what actually happened,
        never asserting what we wish had.
        """
        events = self._log.read_all()
        initiated = next(
            e for e in events
            if e.type == ev.SETTLEMENT_INITIATED
            and e.payload["settlement_id"] == drift.settlement_id
        )
        order_id = initiated.payload["razorpay_order_id"]

        payment_id = None
        for item in self._client.order.payments(order_id).get("items", []):
            if item.get("status") == "captured":
                payment_id = item["id"]
                break

        self._log.append(
            ACCOUNTANT_ACTOR_ID, ev.SETTLEMENT_COMPLETED,
            {"settlement_id": drift.settlement_id, "razorpay_payment_id": payment_id},
            correlation_id=initiated.correlation_id,
            causation_id=initiated.event_id,
        )

    def resume(self, actor_id: str) -> None:
        self._log.append(
            ACCOUNTANT_ACTOR_ID, ev.ACTOR_RESUMED,
            {"actor_id": actor_id},
            correlation_id=f"freeze_{actor_id}",
        )
```

- [ ] **Step 4: Teach the fold about freezing**

In `src/exchange/projections.py`, inside `fold_from`'s branch chain, add:

```python
        elif event.type == ev.ACTOR_FROZEN:
            existing = actors.get(p["actor_id"])
            if existing is not None:
                actors[p["actor_id"]] = replace(existing, status=ActorStatus.FROZEN)

        elif event.type == ev.ACTOR_RESUMED:
            existing = actors.get(p["actor_id"])
            if existing is not None:
                actors[p["actor_id"]] = replace(existing, status=ActorStatus.ACTIVE)
```

A freeze for an unknown actor is ignored rather than raising — the same shape as `ORDER_FILLED` for an unknown order.

- [ ] **Step 5: Run the suite and commit**

```bash
.venv/bin/pytest -q
git add src/exchange/house/accountant.py src/exchange/projections.py tests/test_accountant.py
git commit -m "feat: freeze, repair from the remote truth, resume"
```

---

### Task 11: End to end — a broker buys an insight

The acceptance proof: a house agent mints from real settled activity, brokers value the lot in their own words, the auction clears second-price, and points move.

**Files:**
- Create: `tests/test_intelligence_end_to_end.py`
- Create: `scripts/intelligence_demo.py`

- [ ] **Step 1: Write the end-to-end tests**

Create `tests/test_intelligence_end_to_end.py`:

```python
import pytest

from exchange.eventlog import EventLog
from exchange.events import SETTLEMENT_COMPLETED, SETTLEMENT_INITIATED
from exchange.house.accountant import Accountant
from exchange.house.agent import HouseAgent
from exchange.house.auction import run_auction
from exchange.house.points import points_for_settlement, royalty_for
from exchange.llm.scripted import ScriptedProvider
from tests.test_rails import FakeRazorpay

CORR = "corr_intel"


@pytest.fixture
def log(tmp_path):
    lg = EventLog(str(tmp_path / "intel.db"))
    for i in range(30):
        lg.append(f"m_{i}", SETTLEMENT_INITIATED,
                  {"settlement_id": f"s{i}", "match_id": f"m{i}", "currency": "INR",
                   "amount": 380_000, "razorpay_order_id": f"order_{i}"},
                  correlation_id=f"c{i}")
        lg.append(f"m_{i}", SETTLEMENT_COMPLETED,
                  {"settlement_id": f"s{i}", "razorpay_payment_id": f"pay_{i}"},
                  correlation_id=f"c{i}")
    yield lg
    lg.close()


def test_a_lot_is_minted_auctioned_and_cleared_on_one_correlation_id(log):
    from exchange.house.auction import Bid

    house = HouseAgent(log, ScriptedProvider(["skincare AOV up 12% week on week"]))
    lot = house.mint_from(house.observe(), CORR)
    assert lot is not None

    result = run_auction(log, lot.asset_id,
                         [Bid("m_1", 800, "small category for us"),
                          Bid("m_2", 1850, "we spend 40k a month here"),
                          Bid("m_3", 1200, "worth a look")],
                         correlation_id=CORR)

    assert result.winner_id == "m_2"
    assert result.price == 1200, "second price, not the winner's own bid"

    types = [e.type for e in log.read_by_correlation(CORR)]
    assert types[0] == "INSIGHT_MINTED"
    assert types[-1] == "AUCTION_CLEARED"


def test_the_free_headline_is_public_and_the_playbook_is_not(log):
    house = HouseAgent(log, ScriptedProvider(["skincare AOV up 12%"]))
    house.mint_from(house.observe(), CORR)

    assert house.feed() == ("skincare AOV up 12%",)
    minted = [e for e in log.read_by_correlation(CORR)
              if e.type == "INSIGHT_MINTED"][0]
    assert "playbook" not in minted.payload


def test_contributors_earn_when_their_win_is_bought(log):
    """What turns the product from extraction into a deal."""
    house = HouseAgent(log, ScriptedProvider(["skincare AOV up 12%"]))
    lot = house.mint_from(house.observe(), CORR)

    per_contributor = royalty_for(1200, len(lot.spec["contributor_ids"]))

    assert per_contributor > 0
    assert per_contributor * len(lot.spec["contributor_ids"]) <= 1200


def test_a_sharp_small_trade_out_earns_a_sloppy_large_one(log):
    sharp = points_for_settlement(190_000, ask_price=1940, qty=100, delivered=True)
    sloppy = points_for_settlement(1_940_000, ask_price=1940, qty=1000, delivered=True)

    assert sharp > sloppy


def test_the_accountant_finds_nothing_wrong_with_a_clean_run(log):
    client = FakeRazorpay(payments_by_order={
        f"order_{i}": {"count": 1, "items": [{"id": f"pay_{i}", "status": "captured"}]}
        for i in range(30)
    })

    accountant = Accountant(log, client)

    assert accountant.reconcile() == []
```

- [ ] **Step 2: Run them**

Run: `.venv/bin/pytest tests/test_intelligence_end_to_end.py -v`
Expected: 5 passed. Fix the underlying module on any failure, never the assertion.

- [ ] **Step 3: Write the demo script**

Create `scripts/intelligence_demo.py`:

```python
"""Mint a lot from real settled activity, auction it, and show the trail.

  LLM_PROVIDER=ollama .venv/bin/python scripts/intelligence_demo.py

Reads whatever `runs/brokers.db` already holds, so run the broker demo first
for real settled trades to mine. No Razorpay call is made.
"""
from __future__ import annotations

import sys

from dotenv import load_dotenv

from exchange.eventlog import EventLog
from exchange.house.accountant import Accountant
from exchange.house.agent import HouseAgent
from exchange.house.auction import Bid, run_auction
from exchange.house.points import royalty_for
from exchange.ids import new_id
from exchange.llm.openai_compat import providers_from_env


def main() -> int:
    load_dotenv()
    strong, _fast = providers_from_env()
    correlation_id = new_id("corr")
    print(f"Correlation id: {correlation_id}\n")

    log = EventLog("runs/brokers.db")
    house = HouseAgent(log, strong)

    observations = house.observe()
    print(f"=== WHAT RAZORPAY CAN SEE ===\n  {len(observations)} settled trades "
          f"across {len({o['actor_id'] for o in observations})} merchants\n")

    lot = house.mint_from(observations, correlation_id)
    if lot is None:
        refused = [e for e in log.read_by_correlation(correlation_id)
                   if e.type == "PRIVACY_REFUSED"][0]
        print("=== PRIVACY FLOOR ===")
        print(f"  REFUSED: {refused.payload['reason']}")
        print("  This is the floor working. Run more trades and try again.\n")
        log.close()
        return 0

    print(f"=== VICTORY FEED (free) ===\n  {lot.spec['headline']}\n")

    print("=== AUCTION (points, sealed, second price) ===")
    result = run_auction(log, lot.asset_id, [
        Bid("m_buyer", 1850, "we spend heavily in exactly this category"),
        Bid("m_rival", 1200, "worth a look but not core to us"),
        Bid("m_quiet", 800, "small for us this quarter"),
    ], correlation_id=correlation_id)
    print(f"  winner: {result.winner_id}  pays: {result.price}  ({result.reason})")
    print(f"  each of {len(lot.spec['contributor_ids'])} contributors earns "
          f"{royalty_for(result.price or 0, len(lot.spec['contributor_ids']))}\n")

    print("=== THE BOOKS ===")
    violations = Accountant(log, None).assert_invariants()
    print(f"  invariant violations: {len(violations)}")
    for v in violations:
        print(f"    {v.kind}: {v.detail}")
    print()

    print("=== AUDIT TRAIL ===")
    for event in log.read_by_correlation(correlation_id):
        print(f"  [{event.seq:>3}] {event.actor_id:<12} {event.type}")

    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run it**

```bash
LLM_PROVIDER=ollama .venv/bin/python scripts/intelligence_demo.py
```

Expected: either a minted lot with a cleared auction, or a visible privacy refusal if the database holds fewer than 25 distinct merchants. **Both are correct outcomes** — the refusal is the floor doing its job, and it is worth showing.

- [ ] **Step 5: Commit**

```bash
git add tests/test_intelligence_end_to_end.py scripts/intelligence_demo.py
git commit -m "test: a lot is minted, auctioned and cleared on one correlation id"
```

---

## Phasing across two days

| Day | Tasks | Deliverable |
|---|---|---|
| 7 | 1–5 | Carried fixes; agents choose; per-tier providers; house agent, insight lots, privacy floor, victory feed |
| 8 | 6–11 | Auction with Scout valuation; points; the accountant with freeze-repair-resume; end to end |

**If day 8 slips, cut Task 8's earning formula first** — award a flat stipend so auctions still run. **Task 10 is never droppable**: it is the failure demo, it is the delivery signal the memory loop needs, and it is the only thing proving the incremental projection is honest.

## Plan 3 done when

- `.venv/bin/pytest` is green.
- A lot is minted from real settled activity, auctioned second-price, and cleared — all on one `correlation_id`.
- The privacy floor visibly refuses a lot derived from too few merchants.
- The accountant detects a drifted settlement, freezes, repairs from Razorpay's own record, and resumes.
- No hand-set weight decides which counterparty or which lot an agent picks.

## What Plan 4 builds on

- `Accountant.reconcile` is the delivery signal — Plan 4 feeds its result to `RelationshipGraph.apply_lesson` so reliability lessons finally move standing.
- `points_for_settlement` is the tuning surface for "make the run interesting"; the constants are deliberately at the top of the module.
- The choosing agent's `COUNTERPARTY_CHOSEN` events are the labels a learned ranker would need. Plan 4's run produces them in quantity.
