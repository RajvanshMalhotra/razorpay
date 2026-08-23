# Broker and Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every merchant a broker that can find supply, judge a counterparty, negotiate, decide when to walk away, and remember how it went — running on a memory engine that never replays the whole log to know the present.

**Architecture:** A provider-agnostic LLM interface (Ollama locally, `deepseek-v4-pro` in production) under four agent roles: an orchestrator plus Trader, Scout and Diplomat in isolated contexts, and a Subconscious that only remembers. Context is stored as semantic deltas over a tree with checkpoints at episode boundaries, never as copied transcripts.

**Tech Stack:** Python 3.11+, `openai` SDK (OpenAI-compatible, points at both Ollama and DeepSeek), SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-08-23-broker-and-memory-design.md`

## Global Constraints

- Python 3.11 or newer.
- Razorpay **test mode only**. Keys in gitignored `.env`.
- The event log is the single source of truth; all other state is a projection folded from it.
- Every money action emits a `PolicyDecision` **before** executing. Brokers call `Exchange.execute_match`; they never touch a rail directly.
- Amounts are integers in minor units. Never floats.
- **Every test injects a scripted LLM provider.** No test may make a network call. This mirrors `fake_embedder` and `FakeRazorpay` from Plan 1.
- **Context deltas are additive-only on `facts` and `decisions`.** The only removable field is `unresolved`. This is enforced by construction — no removal field exists for the others.
- Default model is `deepseek-v4-pro`; local development uses Ollama. Model choice is config, never hardcoded in agent code.
- The task ends with a commit.

---

### Task 1: Carried defects from Plan 1

Two real defects recorded in `learnings_tradeoffs.md`. Brokers loop over the open book, so they will hit both immediately.

**Files:**
- Modify: `src/exchange/models.py` (add `qty` to `Match`)
- Modify: `src/exchange/service.py` (`_record_fill`)
- Modify: `src/exchange/matching.py` (populate `Match.qty`)
- Test: `tests/test_service.py`, `tests/test_matching.py`

**Interfaces:**
- Consumes: `Match`, `Exchange._record_fill` from Plan 1.
- Produces: `Match.qty: int`; `_record_fill` emits per-side quantity and actor.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_matching.py`:

```python
def test_match_carries_the_filled_quantity():
    asks = [_ask("ord_2", "m_b", "ast_2", 1940, qty=1000)]

    match = find_candidates(BID, asks, ASSETS, _index())[0]

    assert match.qty == 500  # the bid's qty, which is what actually trades
```

Append to `tests/test_service.py`:

```python
def test_fill_is_recorded_for_the_ask_even_when_the_bid_is_not_in_the_book(exchange):
    """The ask must still be depleted, or it can be re-settled forever."""
    ask = Order(
        order_id="ord_ask", actor_id="m_seller", side=Side.ASK, asset_ref="ast_1",
        asset_query=None, qty=1000, limit_price=1940, currency=Currency.INR,
        expires_at="2026-09-30T00:00:00+00:00", policy_snapshot={},
    )
    exchange.post_order(ask, correlation_id="c1")
    # note: the bid is deliberately NOT posted

    match = Match(
        match_id="mch_1", bid_order_id="ord_absent_bid", ask_order_id="ord_ask",
        clearing_price=1940, qty=400, score=0.9, rationale="test",
    )
    exchange.execute_match(match, "m_buyer", "m_seller", TRUSTED, correlation_id="c1")

    assert exchange.state().open_orders["ord_ask"].qty == 600


def test_each_side_of_a_fill_is_attributed_to_its_own_actor(exchange):
    bid = Order(
        order_id="ord_bid", actor_id="m_buyer", side=Side.BID, asset_ref=None,
        asset_query={"text": "mailers"}, qty=400, limit_price=2200,
        currency=Currency.INR, expires_at="2026-09-30T00:00:00+00:00",
        policy_snapshot={},
    )
    ask = Order(
        order_id="ord_ask", actor_id="m_seller", side=Side.ASK, asset_ref="ast_1",
        asset_query=None, qty=1000, limit_price=1940, currency=Currency.INR,
        expires_at="2026-09-30T00:00:00+00:00", policy_snapshot={},
    )
    exchange.post_order(bid, correlation_id="c1")
    exchange.post_order(ask, correlation_id="c1")

    match = Match(
        match_id="mch_1", bid_order_id="ord_bid", ask_order_id="ord_ask",
        clearing_price=1940, qty=400, score=0.9, rationale="test",
    )
    exchange.execute_match(match, "m_buyer", "m_seller", TRUSTED, correlation_id="c1")

    fills = [e for e in exchange.log.read_by_correlation("c1") if e.type == "ORDER_FILLED"]
    by_order = {e.payload["order_id"]: e for e in fills}

    assert by_order["ord_bid"].actor_id == "m_buyer"
    assert by_order["ord_ask"].actor_id == "m_seller"
    assert by_order["ord_bid"].payload["qty"] == 400
    assert by_order["ord_ask"].payload["qty"] == 400
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_matching.py::test_match_carries_the_filled_quantity tests/test_service.py -k "fill" -v`
Expected: FAIL — `Match` has no `qty` field.

- [ ] **Step 3: Add `qty` to `Match`**

In `src/exchange/models.py`, add the field to `Match` after `clearing_price`:

```python
@dataclass(frozen=True)
class Match:
    match_id: str
    bid_order_id: str
    ask_order_id: str
    clearing_price: int
    qty: int
    score: float
    rationale: str
```

- [ ] **Step 4: Populate it in the matching engine**

In `src/exchange/matching.py`, inside the `Match(...)` construction, add `qty=bid.qty` immediately after `clearing_price=ask.limit_price`.

- [ ] **Step 5: Fix `_record_fill`**

Replace the body of `_record_fill` in `src/exchange/service.py` with a version that handles each side independently and takes the quantity from the match:

```python
    def _record_fill(self, match: Match, buyer_id: str, seller_id: str,
                     correlation_id: str, causation_id: str) -> None:
        """Deplete both orders. Each side is recorded against its own actor.

        Sides are handled independently on purpose: if one order is already gone
        from the book, the other must still be depleted, or it can be re-settled
        against the same inventory indefinitely.
        """
        book = self.state().open_orders
        for order_id, actor_id in (
            (match.bid_order_id, buyer_id),
            (match.ask_order_id, seller_id),
        ):
            if order_id not in book:
                continue
            self.log.append(
                actor_id,
                ev.ORDER_FILLED,
                {"order_id": order_id, "qty": match.qty},
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
```

- [ ] **Step 6: Update every other `Match(...)` construction**

`qty` is now required. Run `.venv/bin/pytest -q` and fix each `TypeError` by adding `qty=` to that construction — use the bid's quantity where a bid exists, or `500` in fixtures that have no meaningful quantity. Do not give `qty` a default; a silent zero would deplete nothing.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass, count risen by 3.

- [ ] **Step 8: Commit**

```bash
git add src/exchange/models.py src/exchange/service.py src/exchange/matching.py tests/
git commit -m "fix: deplete each side of a fill independently, with its own actor and qty"
```

---

### Task 2: Provider-agnostic LLM interface

One interface over Ollama (local) and DeepSeek (production). Every agent in this plan calls through it and none knows which provider is behind it.

**Files:**
- Create: `src/exchange/llm/__init__.py`
- Create: `src/exchange/llm/base.py`
- Create: `src/exchange/llm/openai_compat.py`
- Create: `src/exchange/llm/scripted.py`
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `exchange.llm.base.LLMMessage(role: str, content: str)` — frozen dataclass
  - `exchange.llm.base.LLMResponse(text: str, input_tokens: int, output_tokens: int, model: str)` — frozen dataclass
  - `exchange.llm.base.LLMProvider` — Protocol with `complete(messages, *, system=None, max_tokens=1024, reasoning_effort=None) -> LLMResponse`
  - `exchange.llm.openai_compat.OpenAICompatProvider(base_url, api_key, model, timeout=120.0)`
  - `exchange.llm.openai_compat.provider_from_env() -> LLMProvider`
  - `exchange.llm.scripted.ScriptedProvider(responses: list[str])` with `.calls: list[dict]`

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add `"openai>=1.40"` to `[project] dependencies`. Then:

```bash
.venv/bin/pip install "openai>=1.40"
```

- [ ] **Step 2: Extend `.env.example`**

Append:

```bash
# LLM provider: "ollama" (local) or "deepseek" (production)
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:7b
DEEPSEEK_API_KEY=
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_llm.py`:

```python
import pytest

from exchange.llm.base import LLMMessage, LLMResponse
from exchange.llm.scripted import ScriptedProvider


def test_scripted_provider_returns_responses_in_order():
    provider = ScriptedProvider(["first", "second"])

    a = provider.complete([LLMMessage("user", "hi")])
    b = provider.complete([LLMMessage("user", "again")])

    assert a.text == "first"
    assert b.text == "second"


def test_scripted_provider_records_what_it_was_asked():
    provider = ScriptedProvider(["ok"])

    provider.complete([LLMMessage("user", "what is the price?")], system="You trade.")

    call = provider.calls[0]
    assert call["system"] == "You trade."
    assert call["messages"][0].content == "what is the price?"


def test_scripted_provider_raises_when_exhausted():
    """A test that makes more calls than it scripted has a bug in the test."""
    provider = ScriptedProvider(["only one"])
    provider.complete([LLMMessage("user", "hi")])

    with pytest.raises(RuntimeError, match="exhausted"):
        provider.complete([LLMMessage("user", "hi again")])


def test_scripted_provider_reports_token_counts():
    provider = ScriptedProvider(["hello there"])

    response = provider.complete([LLMMessage("user", "hi")])

    assert response.input_tokens > 0
    assert response.output_tokens > 0
    assert response.model == "scripted"


def test_llm_message_is_frozen():
    message = LLMMessage("user", "hi")

    with pytest.raises(Exception):
        message.content = "changed"
```

- [ ] **Step 4: Run to verify failure**

Run: `.venv/bin/pytest tests/test_llm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'exchange.llm'`

- [ ] **Step 5: Implement `base.py`**

Create `src/exchange/llm/__init__.py` empty, then `src/exchange/llm/base.py`:

```python
"""The LLM interface every agent calls through.

Agents never know which provider is behind this. Local development runs
Ollama; production runs DeepSeek. Both speak the OpenAI-compatible wire
format, so one implementation covers both — but the Protocol is what agent
code depends on, so a third provider costs one new class and nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


class LLMProvider(Protocol):
    def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        ...
```

- [ ] **Step 6: Implement `scripted.py`**

Create `src/exchange/llm/scripted.py`:

```python
"""A provider that returns canned text, for tests.

Every test in this project injects one of these. No test may reach the
network — the same discipline as `fake_embedder` and `FakeRazorpay`.
"""
from __future__ import annotations

from exchange.llm.base import LLMMessage, LLMResponse


class ScriptedProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._index = 0
        self.calls: list[dict] = []

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        self.calls.append({
            "messages": messages,
            "system": system,
            "max_tokens": max_tokens,
            "reasoning_effort": reasoning_effort,
        })
        if self._index >= len(self._responses):
            raise RuntimeError(
                f"ScriptedProvider exhausted after {len(self._responses)} responses; "
                f"call {self._index + 1} has nothing to return"
            )
        text = self._responses[self._index]
        self._index += 1
        prompt_chars = sum(len(m.content) for m in messages) + len(system or "")
        return LLMResponse(
            text=text,
            input_tokens=max(1, prompt_chars // 4),
            output_tokens=max(1, len(text) // 4),
            model="scripted",
        )
```

- [ ] **Step 7: Implement `openai_compat.py`**

Create `src/exchange/llm/openai_compat.py`:

```python
"""OpenAI-compatible provider — covers both Ollama and DeepSeek.

Ollama serves an OpenAI-compatible endpoint at /v1, and DeepSeek's API is
OpenAI-compatible too, so the same client class reaches both. They differ
only in base_url, key and model name.
"""
from __future__ import annotations

import os

from openai import OpenAI

from exchange.llm.base import LLMMessage, LLMResponse

OLLAMA_BASE_URL = "http://localhost:11434/v1"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"


class OpenAICompatProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 120.0,
    ) -> None:
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self._model = model

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        payload = []
        if system:
            payload.append({"role": "system", "content": system})
        payload.extend({"role": m.role, "content": m.content} for m in messages)

        extra: dict = {}
        if reasoning_effort:
            extra["reasoning_effort"] = reasoning_effort

        completion = self._client.chat.completions.create(
            model=self._model,
            messages=payload,
            max_tokens=max_tokens,
            **extra,
        )
        usage = completion.usage
        return LLMResponse(
            text=completion.choices[0].message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            model=self._model,
        )


def provider_from_env() -> OpenAICompatProvider:
    """Build a provider from LLM_PROVIDER / LLM_MODEL / DEEPSEEK_API_KEY.

    Callers must have loaded .env themselves — this reads the environment
    only, for the same reason Config.from_env does.
    """
    which = os.environ.get("LLM_PROVIDER", "ollama").lower()

    if which == "ollama":
        return OpenAICompatProvider(
            base_url=OLLAMA_BASE_URL,
            api_key="ollama",  # Ollama ignores it, the SDK requires one
            model=os.environ.get("LLM_MODEL", DEFAULT_OLLAMA_MODEL),
        )

    if which == "deepseek":
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            raise ValueError("LLM_PROVIDER=deepseek but DEEPSEEK_API_KEY is not set")
        return OpenAICompatProvider(
            base_url=DEEPSEEK_BASE_URL,
            api_key=key,
            model=os.environ.get("LLM_MODEL", DEFAULT_DEEPSEEK_MODEL),
        )

    raise ValueError(f"Unknown LLM_PROVIDER {which!r}; expected 'ollama' or 'deepseek'")
```

- [ ] **Step 8: Run tests**

Run: `.venv/bin/pytest tests/test_llm.py -v`
Expected: 5 passed.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml .env.example src/exchange/llm/ tests/test_llm.py
git commit -m "feat: provider-agnostic LLM interface over Ollama and DeepSeek"
```

---

### Task 3: Context state and additive-only deltas

The semantic unit of agent memory. A context is structured state, not a transcript.

**Files:**
- Create: `src/exchange/agents/__init__.py`
- Create: `src/exchange/agents/context.py`
- Test: `tests/test_context.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `exchange.agents.context.ContextState` — frozen dataclass with `objective: str`, and `constraints`, `decisions`, `facts`, `unresolved`, `artifacts` as `tuple[str, ...]`
  - `exchange.agents.context.ContextDelta` — frozen dataclass with `objective: str | None`, `constraints_added`, `decisions_added`, `facts_added`, `unresolved_added`, `unresolved_removed`, `artifacts_added`, all `tuple[str, ...]`
  - `exchange.agents.context.apply_delta(state: ContextState, delta: ContextDelta) -> ContextState`
  - `exchange.agents.context.render(state: ContextState) -> str`

**The additive-only rule is enforced by construction:** `ContextDelta` has no `facts_removed` or `decisions_removed` field, so no code can express the removal. `unresolved_removed` exists because there, removal *is* the semantics — a question got answered.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_context.py`:

```python
import dataclasses

from exchange.agents.context import ContextDelta, ContextState, apply_delta, render


def test_applying_a_delta_adds_facts():
    state = ContextState(objective="buy mailers", facts=("stock is low",))
    delta = ContextDelta(facts_added=("merchant_41 quotes 1940",))

    result = apply_delta(state, delta)

    assert result.facts == ("stock is low", "merchant_41 quotes 1940")


def test_applying_a_delta_leaves_the_original_untouched():
    state = ContextState(facts=("a",))

    apply_delta(state, ContextDelta(facts_added=("b",)))

    assert state.facts == ("a",)


def test_a_resolved_question_can_be_removed():
    state = ContextState(unresolved=("what is their lead time?", "do they ship north?"))
    delta = ContextDelta(unresolved_removed=("what is their lead time?",))

    result = apply_delta(state, delta)

    assert result.unresolved == ("do they ship north?",)


def test_removing_an_unresolved_question_that_is_absent_is_harmless():
    state = ContextState(unresolved=("a",))

    result = apply_delta(state, ContextDelta(unresolved_removed=("b",)))

    assert result.unresolved == ("a",)


def test_there_is_no_way_to_remove_a_fact():
    """The additive-only rule is structural: the field does not exist."""
    fields = {f.name for f in dataclasses.fields(ContextDelta)}

    assert "facts_removed" not in fields
    assert "decisions_removed" not in fields


def test_objective_is_replaced_not_appended():
    state = ContextState(objective="buy mailers")

    result = apply_delta(state, ContextDelta(objective="buy boxes instead"))

    assert result.objective == "buy boxes instead"


def test_objective_is_kept_when_the_delta_does_not_set_it():
    state = ContextState(objective="buy mailers")

    result = apply_delta(state, ContextDelta(facts_added=("x",)))

    assert result.objective == "buy mailers"


def test_duplicate_facts_are_not_added_twice():
    state = ContextState(facts=("a",))

    result = apply_delta(state, ContextDelta(facts_added=("a", "b")))

    assert result.facts == ("a", "b")


def test_render_produces_labelled_sections_for_populated_fields_only():
    state = ContextState(objective="buy mailers", facts=("stock low",))

    text = render(state)

    assert "buy mailers" in text
    assert "stock low" in text
    assert "unresolved" not in text.lower()


def test_render_of_an_empty_state_is_empty():
    assert render(ContextState()) == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'exchange.agents'`

- [ ] **Step 3: Implement**

Create `src/exchange/agents/__init__.py` empty, then `src/exchange/agents/context.py`:

```python
"""Semantic context: structured state plus additive deltas.

Context is not a transcript. It is what the agent knows, in fields, so that
a checkpoint can be materialised and a delta can be applied without keeping
every message ever exchanged.

The additive-only rule is enforced by construction rather than by validation:
`ContextDelta` has no field capable of removing a fact or a decision. The one
removable field is `unresolved`, because there removal is the meaning — a
question got answered. A delta that could drop a fact would quietly rewrite
history, and the agent would have no way to know something was missing.
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ContextState:
    objective: str = ""
    constraints: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextDelta:
    objective: str | None = None
    constraints_added: tuple[str, ...] = ()
    decisions_added: tuple[str, ...] = ()
    facts_added: tuple[str, ...] = ()
    unresolved_added: tuple[str, ...] = ()
    unresolved_removed: tuple[str, ...] = ()
    artifacts_added: tuple[str, ...] = ()


def _extend(existing: tuple[str, ...], added: tuple[str, ...]) -> tuple[str, ...]:
    """Append, preserving order and skipping anything already present."""
    seen = set(existing)
    return existing + tuple(item for item in added if not (item in seen or seen.add(item)))


def apply_delta(state: ContextState, delta: ContextDelta) -> ContextState:
    removed = set(delta.unresolved_removed)
    unresolved = tuple(q for q in state.unresolved if q not in removed)
    return replace(
        state,
        objective=state.objective if delta.objective is None else delta.objective,
        constraints=_extend(state.constraints, delta.constraints_added),
        decisions=_extend(state.decisions, delta.decisions_added),
        facts=_extend(state.facts, delta.facts_added),
        unresolved=_extend(unresolved, delta.unresolved_added),
        artifacts=_extend(state.artifacts, delta.artifacts_added),
    )


_SECTIONS = (
    ("constraints", "Constraints"),
    ("decisions", "Decisions"),
    ("facts", "Known"),
    ("unresolved", "Unresolved"),
    ("artifacts", "Artifacts"),
)


def render(state: ContextState) -> str:
    """Flatten to prompt text. Empty sections are omitted, not shown as empty."""
    parts: list[str] = []
    if state.objective:
        parts.append(f"Objective: {state.objective}")
    for field_name, label in _SECTIONS:
        values = getattr(state, field_name)
        if values:
            listed = "\n".join(f"  - {v}" for v in values)
            parts.append(f"{label}:\n{listed}")
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_context.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/exchange/agents/ tests/test_context.py
git commit -m "feat: semantic context state with additive-only deltas"
```

---

### Task 4: The context tree — nodes, checkpoints, retrieval

**Files:**
- Create: `src/exchange/agents/tree.py`
- Test: `tests/test_tree.py`

**Interfaces:**
- Consumes: `ContextState`, `ContextDelta`, `apply_delta` from Task 3; `new_id` from Plan 1.
- Produces:
  - `exchange.agents.tree.ContextNode` — frozen dataclass: `node_id, parent_id, delta, state_version, checkpoint`
  - `exchange.agents.tree.ContextTree()` with:
    - `add(parent_id: str | None, delta: ContextDelta, state_version: int) -> str`
    - `checkpoint(node_id: str) -> None`
    - `materialise(node_id: str) -> ContextState`
    - `ancestors(node_id: str) -> list[ContextNode]`
    - `node(node_id: str) -> ContextNode`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tree.py`:

```python
import pytest

from exchange.agents.context import ContextDelta
from exchange.agents.tree import ContextTree


def test_a_single_node_materialises_its_own_delta():
    tree = ContextTree()
    node = tree.add(None, ContextDelta(objective="buy mailers"), state_version=1)

    assert tree.materialise(node).objective == "buy mailers"


def test_a_chain_materialises_every_delta_in_order():
    tree = ContextTree()
    a = tree.add(None, ContextDelta(facts_added=("a",)), state_version=1)
    b = tree.add(a, ContextDelta(facts_added=("b",)), state_version=2)
    c = tree.add(b, ContextDelta(facts_added=("c",)), state_version=3)

    assert tree.materialise(c).facts == ("a", "b", "c")


def test_branches_do_not_see_each_others_deltas():
    """Sub-agents branch from a common parent and must stay isolated."""
    tree = ContextTree()
    root = tree.add(None, ContextDelta(facts_added=("shared",)), state_version=1)
    left = tree.add(root, ContextDelta(facts_added=("left only",)), state_version=2)
    right = tree.add(root, ContextDelta(facts_added=("right only",)), state_version=2)

    assert tree.materialise(left).facts == ("shared", "left only")
    assert tree.materialise(right).facts == ("shared", "right only")


def test_a_checkpoint_materialises_the_state_at_that_node():
    tree = ContextTree()
    a = tree.add(None, ContextDelta(facts_added=("a",)), state_version=1)
    b = tree.add(a, ContextDelta(facts_added=("b",)), state_version=2)

    tree.checkpoint(b)

    assert tree.node(b).checkpoint is not None
    assert tree.node(b).checkpoint.facts == ("a", "b")


def test_materialising_past_a_checkpoint_gives_the_same_answer():
    """The checkpoint is an optimisation; it must not change the result."""
    tree = ContextTree()
    a = tree.add(None, ContextDelta(facts_added=("a",)), state_version=1)
    b = tree.add(a, ContextDelta(facts_added=("b",)), state_version=2)
    tree.checkpoint(b)
    c = tree.add(b, ContextDelta(facts_added=("c",)), state_version=3)

    assert tree.materialise(c).facts == ("a", "b", "c")


def test_materialising_stops_walking_at_the_nearest_checkpoint():
    tree = ContextTree()
    node = tree.add(None, ContextDelta(facts_added=("deep",)), state_version=1)
    for i in range(20):
        node = tree.add(node, ContextDelta(facts_added=(f"f{i}",)), state_version=i + 2)
    tree.checkpoint(node)
    tail = tree.add(node, ContextDelta(facts_added=("tail",)), state_version=99)

    assert tree.walk_length(tail) == 2  # the tail node plus the checkpointed node


def test_ancestors_are_returned_nearest_first():
    tree = ContextTree()
    a = tree.add(None, ContextDelta(objective="root"), state_version=1)
    b = tree.add(a, ContextDelta(facts_added=("b",)), state_version=2)
    c = tree.add(b, ContextDelta(facts_added=("c",)), state_version=3)

    assert [n.node_id for n in tree.ancestors(c)] == [b, a]


def test_a_node_records_the_state_version_it_saw():
    tree = ContextTree()
    node = tree.add(None, ContextDelta(), state_version=8412)

    assert tree.node(node).state_version == 8412


def test_an_unknown_node_raises():
    tree = ContextTree()

    with pytest.raises(KeyError):
        tree.materialise("nope")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_tree.py -v`
Expected: FAIL — no module `exchange.agents.tree`.

- [ ] **Step 3: Implement**

Create `src/exchange/agents/tree.py`:

```python
"""The execution tree: how an agent got where it is.

Nodes hold a delta, not a copy of the parent's context. Copying whole
contexts down a chain is quadratic in storage; deltas are linear. A
checkpoint materialises the full state at a node so that reconstruction
walks back only as far as the nearest one instead of to the root.

Retrieval is leaf-first: the current node carries `state_version`, so the
world state is immediate, and ancestors are consulted only when the question
needs history. The leaf says what just happened; ancestors say why.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from exchange.agents.context import ContextDelta, ContextState, apply_delta
from exchange.ids import new_id


@dataclass(frozen=True)
class ContextNode:
    node_id: str
    parent_id: str | None
    delta: ContextDelta
    state_version: int
    checkpoint: ContextState | None = None


class ContextTree:
    def __init__(self) -> None:
        self._nodes: dict[str, ContextNode] = {}

    def add(
        self,
        parent_id: str | None,
        delta: ContextDelta,
        state_version: int,
    ) -> str:
        if parent_id is not None and parent_id not in self._nodes:
            raise KeyError(f"unknown parent node {parent_id!r}")
        node_id = new_id("ctx")
        self._nodes[node_id] = ContextNode(
            node_id=node_id,
            parent_id=parent_id,
            delta=delta,
            state_version=state_version,
        )
        return node_id

    def node(self, node_id: str) -> ContextNode:
        return self._nodes[node_id]

    def ancestors(self, node_id: str) -> list[ContextNode]:
        """Nearest first, excluding the node itself."""
        out: list[ContextNode] = []
        current = self._nodes[node_id].parent_id
        while current is not None:
            node = self._nodes[current]
            out.append(node)
            current = node.parent_id
        return out

    def _chain(self, node_id: str) -> list[ContextNode]:
        """Nodes from the nearest checkpoint (inclusive) down to node_id."""
        chain: list[ContextNode] = []
        current: str | None = node_id
        while current is not None:
            node = self._nodes[current]
            chain.append(node)
            if node.checkpoint is not None:
                break
            current = node.parent_id
        chain.reverse()
        return chain

    def walk_length(self, node_id: str) -> int:
        """How many nodes materialising this one has to touch. Used by tests."""
        return len(self._chain(node_id))

    def materialise(self, node_id: str) -> ContextState:
        chain = self._chain(node_id)
        head = chain[0]
        if head.checkpoint is not None:
            state = head.checkpoint
            rest = chain[1:]
        else:
            state = ContextState()
            rest = chain
        for node in rest:
            state = apply_delta(state, node.delta)
        return state

    def checkpoint(self, node_id: str) -> None:
        """Freeze the full state at this node so later walks stop here."""
        state = self.materialise(node_id)
        self._nodes[node_id] = replace(self._nodes[node_id], checkpoint=state)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_tree.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/exchange/agents/tree.py tests/test_tree.py
git commit -m "feat: context tree with deltas, checkpoints and leaf-first retrieval"
```

---

### Task 5: Sub-agents with isolated contexts

Three acting roles. Each gets its own subtree and its own context; none can read another's working memory. Each returns a **structured summary** that becomes a fact in the parent's delta — narrowing, never merging.

**Files:**
- Create: `src/exchange/agents/subagents.py`
- Test: `tests/test_subagents.py`

**Interfaces:**
- Consumes: `LLMProvider`, `LLMMessage` (Task 2); `ContextState`, `ContextDelta`, `render` (Task 3); `ContextTree` (Task 4).
- Produces:
  - `exchange.agents.subagents.SubAgent(name, system_prompt, provider, tree, parent_id, state_version)` with `.act(instruction: str, facts: tuple[str, ...] = ()) -> str` and `.node_id: str`
  - `exchange.agents.subagents.TRADER_PROMPT`, `SCOUT_PROMPT`, `DIPLOMAT_PROMPT`
  - `exchange.agents.subagents.make_trader / make_scout / make_diplomat(provider, tree, parent_id, state_version) -> SubAgent`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_subagents.py`:

```python
from exchange.agents.context import ContextDelta
from exchange.agents.subagents import (
    SubAgent,
    make_diplomat,
    make_scout,
    make_trader,
)
from exchange.agents.tree import ContextTree
from exchange.llm.scripted import ScriptedProvider


def _tree_with_root():
    tree = ContextTree()
    root = tree.add(None, ContextDelta(objective="trade well"), state_version=1)
    return tree, root


def test_act_returns_the_models_text():
    tree, root = _tree_with_root()
    provider = ScriptedProvider(["merchant_41 quotes 1940"])
    agent = make_trader(provider, tree, root, state_version=1)

    assert agent.act("find packaging") == "merchant_41 quotes 1940"


def test_the_role_prompt_is_sent_as_system():
    tree, root = _tree_with_root()
    provider = ScriptedProvider(["ok"])
    agent = make_diplomat(provider, tree, root, state_version=1)

    agent.act("assess merchant_41")

    assert "diplomat" in provider.calls[0]["system"].lower()


def test_the_inherited_context_reaches_the_prompt():
    tree, root = _tree_with_root()
    provider = ScriptedProvider(["ok"])
    agent = make_trader(provider, tree, root, state_version=1)

    agent.act("find packaging")

    assert "trade well" in provider.calls[0]["messages"][0].content


def test_supplied_facts_reach_the_prompt():
    tree, root = _tree_with_root()
    provider = ScriptedProvider(["ok"])
    agent = make_scout(provider, tree, root, state_version=1)

    agent.act("what is rising?", facts=("vitamin C demand up 12%",))

    assert "vitamin C demand up 12%" in provider.calls[0]["messages"][0].content


def test_acting_records_a_node_under_the_agents_own_branch():
    tree, root = _tree_with_root()
    agent = make_trader(ScriptedProvider(["result"]), tree, root, state_version=7)

    agent.act("find packaging")

    assert tree.node(agent.node_id).parent_id is not None
    assert tree.node(agent.node_id).state_version == 7


def test_two_sub_agents_cannot_see_each_others_work():
    """The isolation that stops a broker quoting supplier A while talking to B."""
    tree, root = _tree_with_root()
    trader = make_trader(ScriptedProvider(["we paid 1800 to supplier A"]), tree, root, 1)
    diplomat = make_diplomat(ScriptedProvider(["merchant_41 is reliable"]), tree, root, 1)

    trader.act("negotiate with supplier A")
    diplomat.act("assess merchant_41")

    diplomat_context = tree.materialise(diplomat.node_id)
    assert not any("1800" in fact for fact in diplomat_context.facts)


def test_each_act_appends_to_the_agents_own_chain():
    tree, root = _tree_with_root()
    agent = make_trader(ScriptedProvider(["first", "second"]), tree, root, 1)

    agent.act("step one")
    first = agent.node_id
    agent.act("step two")

    assert agent.node_id != first
    assert tree.node(agent.node_id).parent_id == first


def test_the_result_is_recorded_as_a_fact():
    tree, root = _tree_with_root()
    agent = make_scout(ScriptedProvider(["demand is rising"]), tree, root, 1)

    agent.act("check trends")

    assert "demand is rising" in tree.materialise(agent.node_id).facts


def test_reasoning_effort_is_passed_through():
    tree, root = _tree_with_root()
    provider = ScriptedProvider(["ok"])
    agent = SubAgent("test", "You test.", provider, tree, root, 1, reasoning_effort="low")

    agent.act("do it")

    assert provider.calls[0]["reasoning_effort"] == "low"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_subagents.py -v`
Expected: FAIL — no module `exchange.agents.subagents`.

- [ ] **Step 3: Implement**

Create `src/exchange/agents/subagents.py`:

```python
"""The three acting sub-agents, each in its own isolated context.

A single agent holding every concern gets slow, gets confused, and says the
wrong thing in the wrong conversation — quoting what was paid to supplier A
while negotiating with supplier B. Each sub-agent therefore branches from the
orchestrator's node and never reads a sibling's branch.

Results travel upward as structured summaries that become facts in the
parent's delta. That is narrowing, not merging: the parent chooses what to
promote rather than reconciling two versions of the same thing.
"""
from __future__ import annotations

from exchange.agents.context import ContextDelta, apply_delta, render
from exchange.agents.tree import ContextTree
from exchange.llm.base import LLMMessage, LLMProvider

TRADER_PROMPT = """You are the Trader for a merchant on a business-to-business exchange.
You buy what the merchant needs and sell what it has. You care about price, quantity,
delivery terms and whether an offer is actually feasible.
Answer in at most three sentences. State numbers plainly. Do not speculate about
counterparties' motives — that is the Diplomat's job."""

SCOUT_PROMPT = """You are the Scout for a merchant on a business-to-business exchange.
You watch demand signals and market trends and judge what an insight is worth.
Answer in at most three sentences. Say what is rising, how confident you are, and what
you would pay for more detail. Do not negotiate — that is the Trader's job."""

DIPLOMAT_PROMPT = """You are the Diplomat for a merchant on a business-to-business exchange.
You judge counterparties: who has dealt well with us, who pushes hard, who is unknown.
Answer in at most three sentences. You advise; you never veto. An unknown counterparty
is an opportunity to be tried at small size, not a risk to be avoided."""


class SubAgent:
    def __init__(
        self,
        name: str,
        system_prompt: str,
        provider: LLMProvider,
        tree: ContextTree,
        parent_id: str,
        state_version: int,
        reasoning_effort: str | None = None,
    ) -> None:
        self.name = name
        self._system = system_prompt
        self._provider = provider
        self._tree = tree
        self._state_version = state_version
        self._effort = reasoning_effort
        # Branch immediately, so this agent's work never lands on the parent's chain.
        self.node_id = tree.add(parent_id, ContextDelta(), state_version)

    def act(self, instruction: str, facts: tuple[str, ...] = ()) -> str:
        inherited = self._tree.materialise(self.node_id)
        if facts:
            # Per-call facts inform this one action without joining the agent's
            # durable context — applied to a copy, never written to the tree.
            inherited = apply_delta(inherited, ContextDelta(facts_added=facts))

        prompt = render(inherited)
        body = f"{prompt}\n\nTask: {instruction}" if prompt else f"Task: {instruction}"

        response = self._provider.complete(
            [LLMMessage("user", body)],
            system=self._system,
            reasoning_effort=self._effort,
        )

        self.node_id = self._tree.add(
            self.node_id,
            ContextDelta(facts_added=(response.text,)),
            self._state_version,
        )
        return response.text


def make_trader(provider, tree, parent_id, state_version) -> SubAgent:
    return SubAgent("trader", TRADER_PROMPT, provider, tree, parent_id, state_version,
                    reasoning_effort="low")


def make_scout(provider, tree, parent_id, state_version) -> SubAgent:
    return SubAgent("scout", SCOUT_PROMPT, provider, tree, parent_id, state_version,
                    reasoning_effort="low")


def make_diplomat(provider, tree, parent_id, state_version) -> SubAgent:
    return SubAgent("diplomat", DIPLOMAT_PROMPT, provider, tree, parent_id, state_version,
                    reasoning_effort="low")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_subagents.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/exchange/agents/subagents.py tests/test_subagents.py
git commit -m "feat: three sub-agents with isolated contexts that narrow upward"
```

---

### Task 6: Negotiation — reasoning, progress, backstop

**Files:**
- Create: `src/exchange/agents/negotiation.py`
- Test: `tests/test_negotiation.py`

**Interfaces:**
- Consumes: `LLMProvider`, `LLMMessage` (Task 2).
- Produces:
  - `exchange.agents.negotiation.Offer(actor_id: str, price: int, message: str)` — frozen
  - `exchange.agents.negotiation.Outcome(agreed: bool, final_price: int | None, offers: tuple[Offer, ...], ended_reason: str)` — frozen
  - `exchange.agents.negotiation.gap_stalled(offers, lookback=2, epsilon=100) -> bool`
  - `exchange.agents.negotiation.parse_offer(text: str) -> tuple[int | None, bool]` — returns `(price, wants_to_walk)`
  - `exchange.agents.negotiation.negotiate(buyer_id, seller_id, buyer_provider, seller_provider, opening_price, buyer_limit, seller_floor, token_budget=8000) -> Outcome`

**A round is one message.** Each side speaks alternately.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_negotiation.py`:

```python
from exchange.agents.negotiation import (
    Offer,
    gap_stalled,
    negotiate,
    parse_offer,
)
from exchange.llm.scripted import ScriptedProvider


def test_parse_offer_reads_a_price():
    price, walk = parse_offer("I can do PRICE: 1940 on those terms.")

    assert price == 1940
    assert walk is False


def test_parse_offer_detects_walking_away():
    price, walk = parse_offer("WALK: we are too far apart on delivery.")

    assert walk is True


def test_parse_offer_returns_none_when_there_is_no_price():
    price, walk = parse_offer("Tell me more about the volumes first.")

    assert price is None
    assert walk is False


def test_gap_stalled_is_false_while_the_sides_are_closing():
    offers = [
        Offer("buyer", 1800, ""), Offer("seller", 2200, ""),
        Offer("buyer", 1900, ""), Offer("seller", 2000, ""),
    ]

    assert gap_stalled(offers) is False


def test_gap_stalled_is_true_when_the_gap_stops_moving():
    offers = [
        Offer("buyer", 1900, ""), Offer("seller", 2000, ""),
        Offer("buyer", 1901, ""), Offer("seller", 1999, ""),
        Offer("buyer", 1902, ""), Offer("seller", 1998, ""),
    ]

    assert gap_stalled(offers, epsilon=100) is True


def test_gap_stalled_sees_through_oscillation():
    """Each offer moves a lot; the gap does not. Movement is not progress."""
    offers = [
        Offer("buyer", 1900, ""), Offer("seller", 2000, ""),
        Offer("buyer", 2000, ""), Offer("seller", 1900, ""),
        Offer("buyer", 1900, ""), Offer("seller", 2000, ""),
    ]

    assert gap_stalled(offers, epsilon=50) is True


def test_negotiation_agrees_when_the_seller_accepts():
    buyer = ScriptedProvider(["PRICE: 1900 — that is my offer."])
    seller = ScriptedProvider(["PRICE: 1900 — agreed."])

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2000, buyer_limit=2200, seller_floor=1800)

    assert outcome.agreed is True
    assert outcome.final_price == 1900
    assert outcome.ended_reason == "agreed"


def test_an_agent_can_walk_away_and_the_reason_is_kept():
    buyer = ScriptedProvider(["WALK: the gap is not worth another round."])
    seller = ScriptedProvider(["PRICE: 2100"])

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2100, buyer_limit=2200, seller_floor=1800)

    assert outcome.agreed is False
    assert "walked" in outcome.ended_reason
    assert "not worth another round" in outcome.offers[-1].message


def test_a_stalled_negotiation_ends_without_agreement():
    buyer = ScriptedProvider(["PRICE: 1900", "PRICE: 1901", "PRICE: 1902", "PRICE: 1903"])
    seller = ScriptedProvider(["PRICE: 2000", "PRICE: 1999", "PRICE: 1998", "PRICE: 1997"])

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2000, buyer_limit=2200, seller_floor=1800)

    assert outcome.agreed is False
    assert outcome.ended_reason == "stalled"


def test_the_token_budget_backstops_a_runaway():
    """Should never fire in a healthy run. If it does, it is a bug upstream."""
    buyer = ScriptedProvider(["PRICE: 1900"] * 50)
    seller = ScriptedProvider(["PRICE: 2100"] * 50)

    outcome = negotiate("m_buyer", "m_seller", buyer, seller,
                        opening_price=2000, buyer_limit=2200, seller_floor=1800,
                        token_budget=200)

    assert outcome.agreed is False
    assert outcome.ended_reason == "token budget exhausted"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_negotiation.py -v`
Expected: FAIL — no module `exchange.agents.negotiation`.

- [ ] **Step 3: Implement**

Create `src/exchange/agents/negotiation.py`:

```python
"""Two brokers haggling, and four reasons it can stop.

A hard round cap makes brokers look like scripts and yields no signal — a
counter tells you that it stopped, never why. Instead:

  reasoning  the agent decides the gap is not worth another round
  progress   the gap between the sides has stopped moving
  backstop   the token budget is exhausted; should never fire
  (the wall clock bounds the whole market run, not a single negotiation)

Progress is measured on the GAP between the two sides, not on each offer.
Oscillation — 1900, 2000, 1900 — moves every offer a lot and closes nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from exchange.llm.base import LLMMessage, LLMProvider

_PRICE = re.compile(r"PRICE:\s*(\d+)", re.IGNORECASE)
_WALK = re.compile(r"\bWALK\b", re.IGNORECASE)

NEGOTIATOR_PROMPT = """You are negotiating a single business-to-business trade.

Reply with ONE of:
  PRICE: <integer in paise> followed by one short sentence of reasoning
  WALK: followed by one short sentence saying why you are ending this

Walk away when the remaining gap is not worth another exchange, when the other
side has stopped moving, or when you have a better option. Do not walk away
merely because the other side is haggling — that is normal.
Never explain your limit. One or two sentences only."""


@dataclass(frozen=True)
class Offer:
    actor_id: str
    price: int
    message: str


@dataclass(frozen=True)
class Outcome:
    agreed: bool
    final_price: int | None
    offers: tuple[Offer, ...]
    ended_reason: str


def parse_offer(text: str) -> tuple[int | None, bool]:
    """Return (price, wants_to_walk). A walk beats a price if both appear."""
    if _WALK.search(text):
        return None, True
    match = _PRICE.search(text)
    return (int(match.group(1)) if match else None), False


def gap_stalled(offers, lookback: int = 2, epsilon: int = 100) -> bool:
    """True when the distance between the two sides has stopped closing.

    Needs at least lookback+1 completed pairs to judge. Measured on the gap,
    never on individual offers — a side can move a lot and concede nothing.
    """
    pairs = []
    for i in range(1, len(offers)):
        if offers[i].actor_id != offers[i - 1].actor_id:
            pairs.append(abs(offers[i].price - offers[i - 1].price))
    if len(pairs) < lookback + 1:
        return False
    recent = pairs[-(lookback + 1):]
    return all(abs(recent[i] - recent[i - 1]) < epsilon for i in range(1, len(recent)))


def negotiate(
    buyer_id: str,
    seller_id: str,
    buyer_provider: LLMProvider,
    seller_provider: LLMProvider,
    opening_price: int,
    buyer_limit: int,
    seller_floor: int,
    token_budget: int = 8000,
) -> Outcome:
    offers: list[Offer] = []
    spent = 0
    transcript: list[str] = [f"Opening ask: {opening_price}"]

    turn = "buyer"
    while True:
        if spent >= token_budget:
            return Outcome(False, None, tuple(offers), "token budget exhausted")

        if turn == "buyer":
            provider, actor, limit_line = (
                buyer_provider, buyer_id, f"You will not pay above {buyer_limit}.",
            )
        else:
            provider, actor, limit_line = (
                seller_provider, seller_id, f"You will not sell below {seller_floor}.",
            )

        response = provider.complete(
            [LLMMessage("user", "\n".join(transcript) + f"\n\n{limit_line}\nYour reply:")],
            system=NEGOTIATOR_PROMPT,
            max_tokens=256,
        )
        spent += response.input_tokens + response.output_tokens

        price, walking = parse_offer(response.text)

        if walking:
            offers.append(Offer(actor, offers[-1].price if offers else opening_price,
                                response.text))
            return Outcome(False, None, tuple(offers), f"{actor} walked away")

        if price is None:
            transcript.append(f"{actor}: {response.text}")
            turn = "seller" if turn == "buyer" else "buyer"
            continue

        offers.append(Offer(actor, price, response.text))
        transcript.append(f"{actor}: {response.text}")

        if len(offers) >= 2 and offers[-1].price == offers[-2].price:
            return Outcome(True, price, tuple(offers), "agreed")

        if gap_stalled(offers):
            return Outcome(False, None, tuple(offers), "stalled")

        turn = "seller" if turn == "buyer" else "buyer"
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_negotiation.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/exchange/agents/negotiation.py tests/test_negotiation.py
git commit -m "feat: negotiation ends by reasoning, stalled progress or token backstop"
```

---

### Task 7: The Subconscious — consolidate and recall

**Files:**
- Create: `src/exchange/agents/subconscious.py`
- Test: `tests/test_subconscious.py`

**Interfaces:**
- Consumes: `LLMProvider`, `LLMMessage` (Task 2); `ContextState` (Task 3).
- Produces:
  - `exchange.agents.subconscious.Lesson(counterparty_id, category, text, kind)` — frozen; `kind` is `"behavioural"` or `"reliability"`
  - `exchange.agents.subconscious.Subconscious(provider)` with:
    - `consolidate(episode: ContextState, counterparty_id: str, category: str) -> Lesson`
    - `recall(counterparty_id: str, category: str | None = None) -> tuple[str, ...]`
    - `lessons: tuple[Lesson, ...]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_subconscious.py`:

```python
from exchange.agents.context import ContextState
from exchange.agents.subconscious import Subconscious
from exchange.llm.scripted import ScriptedProvider


EPISODE = ContextState(
    objective="buy 500 mailers",
    facts=("merchant_41 opened at 2200", "settled at 1940", "delivery slipped two days"),
)


def test_consolidate_produces_a_lesson_from_the_episode():
    provider = ScriptedProvider(["BEHAVIOURAL: pushes hard on delivery dates"])
    sub = Subconscious(provider)

    lesson = sub.consolidate(EPISODE, "merchant_41", "packaging")

    assert lesson.counterparty_id == "merchant_41"
    assert lesson.category == "packaging"
    assert "delivery" in lesson.text


def test_a_behavioural_lesson_is_marked_behavioural():
    sub = Subconscious(ScriptedProvider(["BEHAVIOURAL: haggles hard then folds"]))

    lesson = sub.consolidate(EPISODE, "merchant_41", "packaging")

    assert lesson.kind == "behavioural"


def test_a_reliability_lesson_is_marked_reliability():
    """Only these should move a counterparty's reliability score."""
    sub = Subconscious(ScriptedProvider(["RELIABILITY: did not deliver on time"]))

    lesson = sub.consolidate(EPISODE, "merchant_41", "packaging")

    assert lesson.kind == "reliability"


def test_an_unlabelled_lesson_defaults_to_behavioural():
    """Behavioural is the safe default — it does not cost anyone their score."""
    sub = Subconscious(ScriptedProvider(["they seem to prefer volume deals"]))

    lesson = sub.consolidate(EPISODE, "merchant_41", "packaging")

    assert lesson.kind == "behavioural"


def test_the_episode_reaches_the_model():
    provider = ScriptedProvider(["BEHAVIOURAL: x"])
    sub = Subconscious(provider)

    sub.consolidate(EPISODE, "merchant_41", "packaging")

    assert "settled at 1940" in provider.calls[0]["messages"][0].content


def test_recall_returns_lessons_for_that_counterparty_only():
    sub = Subconscious(ScriptedProvider([
        "BEHAVIOURAL: 41 pushes on delivery",
        "BEHAVIOURAL: 09 pays early",
    ]))
    sub.consolidate(EPISODE, "merchant_41", "packaging")
    sub.consolidate(EPISODE, "merchant_09", "packaging")

    recalled = sub.recall("merchant_41")

    assert any("41 pushes" in r for r in recalled)
    assert not any("09 pays" in r for r in recalled)


def test_recall_can_narrow_to_a_category():
    sub = Subconscious(ScriptedProvider([
        "BEHAVIOURAL: slow on packaging",
        "BEHAVIOURAL: quick on skincare",
    ]))
    sub.consolidate(EPISODE, "merchant_41", "packaging")
    sub.consolidate(EPISODE, "merchant_41", "skincare")

    recalled = sub.recall("merchant_41", category="skincare")

    assert recalled == ("quick on skincare",)


def test_recall_for_an_unknown_counterparty_is_empty():
    sub = Subconscious(ScriptedProvider([]))

    assert sub.recall("merchant_never_met") == ()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_subconscious.py -v`
Expected: FAIL — no module `exchange.agents.subconscious`.

- [ ] **Step 3: Implement**

Create `src/exchange/agents/subconscious.py`:

```python
"""The part that only remembers.

It never acts. After an episode it distils what happened into a durable
lesson; before a sub-agent acts it injects the lessons that matter. That is
what makes a broker feel like it has history rather than a fresh mind each
time.

Lessons are split by kind because the two carry different weight.
"They haggle hard" is behavioural — useful, and costs the counterparty
nothing. "They did not deliver" is reliability — and only that kind moves a
counterparty's score. An unlabelled lesson defaults to behavioural, because
the safe failure is to under-punish rather than to mark someone unreliable on
a model's stray word.
"""
from __future__ import annotations

from dataclasses import dataclass

from exchange.agents.context import ContextState, render
from exchange.llm.base import LLMMessage, LLMProvider

CONSOLIDATE_PROMPT = """You review a completed business deal and write down the single
most useful thing to remember about the counterparty for next time.

Begin your answer with exactly one of:
  BEHAVIOURAL:   how they negotiate — haggling, pace, what they respond to
  RELIABILITY:   whether they did what they promised — delivery, payment, quality

Then one sentence. Be specific and concrete. Write nothing else."""


@dataclass(frozen=True)
class Lesson:
    counterparty_id: str
    category: str
    text: str
    kind: str  # "behavioural" | "reliability"


class Subconscious:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider
        self._lessons: list[Lesson] = []

    @property
    def lessons(self) -> tuple[Lesson, ...]:
        return tuple(self._lessons)

    def consolidate(
        self,
        episode: ContextState,
        counterparty_id: str,
        category: str,
    ) -> Lesson:
        response = self._provider.complete(
            [LLMMessage("user", render(episode))],
            system=CONSOLIDATE_PROMPT,
            max_tokens=200,
        )
        text = response.text.strip()

        upper = text.upper()
        if upper.startswith("RELIABILITY:"):
            kind, text = "reliability", text[len("RELIABILITY:"):].strip()
        elif upper.startswith("BEHAVIOURAL:"):
            kind, text = "behavioural", text[len("BEHAVIOURAL:"):].strip()
        else:
            kind = "behavioural"

        lesson = Lesson(counterparty_id, category, text, kind)
        self._lessons.append(lesson)
        return lesson

    def recall(
        self,
        counterparty_id: str,
        category: str | None = None,
    ) -> tuple[str, ...]:
        return tuple(
            lesson.text
            for lesson in self._lessons
            if lesson.counterparty_id == counterparty_id
            and (category is None or lesson.category == category)
        )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_subconscious.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/exchange/agents/subconscious.py tests/test_subconscious.py
git commit -m "feat: subconscious consolidates episodes and recalls them before acting"
```

---

### Task 8: The relationship graph

Counterparty standing, fed by the Subconscious's reliability lessons. Feeds `find_candidates(counterparty_scores=...)` and `PolicyContext.counterparty_confidence`.

**Files:**
- Create: `src/exchange/agents/relationships.py`
- Test: `tests/test_relationships.py`

**Interfaces:**
- Consumes: `Lesson` (Task 7); `RelationshipEdge` from Plan 1's `models.py`.
- Produces:
  - `exchange.agents.relationships.RelationshipGraph()` with:
    - `record_deal(counterparty_id: str, value: int, delivered: bool) -> None`
    - `apply_lesson(lesson: Lesson) -> None`
    - `standing(counterparty_id: str) -> float`
    - `confidence(counterparty_id: str) -> float`
    - `scores() -> dict[str, float]`

**Optimism under uncertainty:** an unknown counterparty scores `0.65`, deliberately above the `0.5` neutral, so the Diplomat actively wants to try them. Confidence starts at `0.0` and rises with deal count, which is what the policy gate uses to cap trial size.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_relationships.py`:

```python
from exchange.agents.relationships import UNKNOWN_STANDING, RelationshipGraph
from exchange.agents.subconscious import Lesson


def test_an_unknown_counterparty_is_scored_optimistically():
    """Above neutral on purpose: the only way to learn is to deal with them."""
    graph = RelationshipGraph()

    assert graph.standing("never_met") == UNKNOWN_STANDING
    assert UNKNOWN_STANDING > 0.5


def test_an_unknown_counterparty_has_no_confidence():
    graph = RelationshipGraph()

    assert graph.confidence("never_met") == 0.0


def test_confidence_rises_with_deals():
    graph = RelationshipGraph()
    before = graph.confidence("m_41")

    for _ in range(3):
        graph.record_deal("m_41", value=100_000, delivered=True)

    assert graph.confidence("m_41") > before


def test_a_delivered_deal_raises_standing():
    graph = RelationshipGraph()
    graph.record_deal("m_41", value=100_000, delivered=True)

    assert graph.standing("m_41") > 0.5


def test_a_failed_delivery_lowers_standing():
    graph = RelationshipGraph()
    graph.record_deal("m_41", value=100_000, delivered=False)

    assert graph.standing("m_41") < UNKNOWN_STANDING


def test_one_bad_deal_does_not_collapse_a_long_record():
    graph = RelationshipGraph()
    for _ in range(10):
        graph.record_deal("m_41", value=100_000, delivered=True)
    strong = graph.standing("m_41")

    graph.record_deal("m_41", value=100_000, delivered=False)

    assert graph.standing("m_41") > strong - 0.2


def test_a_behavioural_lesson_does_not_move_standing():
    """Haggling hard is business, not unreliability."""
    graph = RelationshipGraph()
    graph.record_deal("m_41", value=100_000, delivered=True)
    before = graph.standing("m_41")

    graph.apply_lesson(Lesson("m_41", "packaging", "haggles hard", "behavioural"))

    assert graph.standing("m_41") == before


def test_a_reliability_lesson_moves_standing():
    graph = RelationshipGraph()
    graph.record_deal("m_41", value=100_000, delivered=True)
    before = graph.standing("m_41")

    graph.apply_lesson(Lesson("m_41", "packaging", "did not deliver", "reliability"))

    assert graph.standing("m_41") < before


def test_scores_returns_every_known_counterparty():
    graph = RelationshipGraph()
    graph.record_deal("m_41", value=1, delivered=True)
    graph.record_deal("m_09", value=1, delivered=True)

    assert set(graph.scores()) == {"m_41", "m_09"}


def test_standing_stays_within_zero_and_one():
    graph = RelationshipGraph()
    for _ in range(50):
        graph.record_deal("m_bad", value=1, delivered=False)
    for _ in range(50):
        graph.record_deal("m_good", value=1, delivered=True)

    assert 0.0 <= graph.standing("m_bad") <= 1.0
    assert 0.0 <= graph.standing("m_good") <= 1.0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_relationships.py -v`
Expected: FAIL — no module `exchange.agents.relationships`.

- [ ] **Step 3: Implement**

Create `src/exchange/agents/relationships.py`:

```python
"""Who we have dealt with, and how it went.

Two numbers per counterparty, and they answer different questions.

  standing    how well they have behaved. Feeds the matcher as a soft nudge.
  confidence  how much we actually know. Feeds the policy gate, which caps
              trade size with strangers.

An unknown counterparty is scored ABOVE neutral, not below. The market would
otherwise ossify into cliques: known merchants win all the business, accrue
more history, get preferred harder, and a new merchant never gets a first
deal. Optimism plus a small cap means we try strangers and risk little doing
it.

Evidence is weighted: one bad deal against a long good record barely moves
the number, because one data point is weak evidence, not a verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field

UNKNOWN_STANDING = 0.65
CONFIDENCE_FULL_AT = 5  # deals needed before we consider ourselves informed
RELIABILITY_LESSON_PENALTY = 2  # counts as this many failed deals


@dataclass
class _Record:
    delivered: int = 0
    failed: int = 0
    deals: int = 0
    total_value: int = 0


class RelationshipGraph:
    def __init__(self) -> None:
        self._records: dict[str, _Record] = {}

    def _record(self, counterparty_id: str) -> _Record:
        return self._records.setdefault(counterparty_id, _Record())

    def record_deal(self, counterparty_id: str, value: int, delivered: bool) -> None:
        record = self._record(counterparty_id)
        record.deals += 1
        record.total_value += value
        if delivered:
            record.delivered += 1
        else:
            record.failed += 1

    def apply_lesson(self, lesson) -> None:
        """Only reliability lessons move standing. Behavioural ones are advice."""
        if lesson.kind != "reliability":
            return
        record = self._record(lesson.counterparty_id)
        record.failed += RELIABILITY_LESSON_PENALTY

    def standing(self, counterparty_id: str) -> float:
        record = self._records.get(counterparty_id)
        if record is None or record.deals == 0:
            return UNKNOWN_STANDING
        # Laplace-smoothed success rate: pulls toward neutral when evidence is thin,
        # so a single outcome cannot swing the score to an extreme.
        score = (record.delivered + 1) / (record.delivered + record.failed + 2)
        return max(0.0, min(1.0, score))

    def confidence(self, counterparty_id: str) -> float:
        record = self._records.get(counterparty_id)
        if record is None:
            return 0.0
        return min(1.0, record.deals / CONFIDENCE_FULL_AT)

    def scores(self) -> dict[str, float]:
        return {cid: self.standing(cid) for cid in self._records}
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_relationships.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/exchange/agents/relationships.py tests/test_relationships.py
git commit -m "feat: relationship graph with optimism for strangers and weighted evidence"
```

---

### Task 9: The broker — orchestrator that ties it together

**Files:**
- Create: `src/exchange/agents/broker.py`
- Test: `tests/test_broker.py`

**Interfaces:**
- Consumes: everything from Tasks 2–8, plus `Exchange`, `find_candidates`, `PolicyContext`, `Order`, `Match` from Plan 1.
- Produces:
  - `exchange.agents.broker.Broker(actor_id, exchange, provider, subconscious=None, graph=None)` with:
    - `.tree: ContextTree`, `.graph: RelationshipGraph`, `.subconscious: Subconscious`, `.root_id: str`
    - `find_supply(need_text: str, qty: int, limit_price: int, correlation_id: str) -> list[Match]`
    - `assess(counterparty_id: str) -> str`
    - `close(match: Match, seller_id: str, correlation_id: str) -> tuple[PolicyDecision, Settlement | None]`

**The broker never touches a rail.** `close` goes through `Exchange.execute_match`, which gates first and settles second.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_broker.py`:

```python
import pytest

from exchange.agents.broker import Broker
from exchange.eventlog import EventLog
from exchange.models import (
    Actor, ActorKind, ActorStatus, Asset, AssetKind, Currency, Order, Side, Verdict,
)
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex
from exchange.service import Exchange
from exchange.llm.scripted import ScriptedProvider
from tests.test_rails import FakeRazorpay
from tests.test_retrieval import fake_embedder


@pytest.fixture
def exchange(tmp_path):
    log = EventLog(str(tmp_path / "broker.db"))
    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_b", "status": "captured"}]}
    })
    ex = Exchange(log, HybridIndex(embed_fn=fake_embedder),
                  RazorpayRail(log, fake, poll_attempts=1, poll_interval=0),
                  CreditRail(log))
    for actor_id in ("m_buyer", "m_seller"):
        ex.register_actor(Actor(actor_id=actor_id, kind=ActorKind.MERCHANT))
    ex.list_asset(Asset(asset_id="ast_mailers", kind=AssetKind.GOODS,
                        title="biodegradable mailers compostable poly", spec={},
                        currency=Currency.INR, origin_actor_id="m_seller"))
    ex.post_order(Order(order_id="ord_ask", actor_id="m_seller", side=Side.ASK,
                        asset_ref="ast_mailers", asset_query=None, qty=1000,
                        limit_price=1940, currency=Currency.INR,
                        expires_at="2026-09-30T00:00:00+00:00", policy_snapshot={}),
                  correlation_id="c1")
    yield ex
    log.close()


def test_broker_finds_supply_for_a_plain_language_need(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(["looks feasible"]))

    matches = broker.find_supply("biodegradable compostable mailers", 500, 2200, "c1")

    assert matches
    assert matches[0].ask_order_id == "ord_ask"


def test_finding_supply_posts_a_real_bid_to_the_book(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(["looks feasible"]))

    broker.find_supply("biodegradable compostable mailers", 500, 2200, "c1")

    bids = [o for o in exchange.state().open_orders.values() if o.side == Side.BID]
    assert len(bids) == 1
    assert bids[0].is_descriptive is True


def test_the_diplomat_advises_on_a_counterparty(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(["unknown, try small"]))

    assert "try small" in broker.assess("m_seller")


def test_recall_is_injected_before_the_diplomat_speaks(exchange):
    provider = ScriptedProvider(["BEHAVIOURAL: pushes on delivery", "advice here"])
    broker = Broker("m_buyer", exchange, provider)
    from exchange.agents.context import ContextState
    broker.subconscious.consolidate(ContextState(facts=("x",)), "m_seller", "packaging")

    broker.assess("m_seller")

    assert "pushes on delivery" in provider.calls[-1]["messages"][0].content


def test_closing_a_trade_goes_through_the_gate(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    matches = broker.find_supply("biodegradable compostable mailers", 500, 2200, "c1")

    decision, settlement = broker.close(matches[0], "m_seller", "c1")

    assert decision.verdict == Verdict.ALLOW
    assert settlement is not None
    types = [e.type for e in exchange.log.read_by_correlation("c1")]
    assert types.index("POLICY_DECIDED") < types.index("SETTLEMENT_INITIATED")


def test_a_stranger_is_gated_by_confidence_not_excluded(exchange):
    """The broker has never dealt with m_seller, so confidence is 0."""
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    matches = broker.find_supply("biodegradable compostable mailers", 500, 2200, "c1")

    decision, _ = broker.close(matches[0], "m_seller", "c1")

    assert decision.verdict == Verdict.ALLOW  # 1940 is far under the trial cap
    assert decision.limits_evaluated["counterparty_confidence"] == 0.0


def test_a_closed_trade_updates_the_relationship(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(["ok"]))
    matches = broker.find_supply("biodegradable compostable mailers", 500, 2200, "c1")

    broker.close(matches[0], "m_seller", "c1")

    assert broker.graph.confidence("m_seller") > 0.0


def test_each_sub_agent_gets_its_own_branch(exchange):
    broker = Broker("m_buyer", exchange, ScriptedProvider(["a", "b"]))

    broker.find_supply("mailers", 500, 2200, "c1")
    broker.assess("m_seller")

    trader_node = broker._trader.node_id
    diplomat_node = broker._diplomat.node_id
    assert trader_node != diplomat_node
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_broker.py -v`
Expected: FAIL — no module `exchange.agents.broker`.

- [ ] **Step 3: Implement**

Create `src/exchange/agents/broker.py`:

```python
"""One merchant's representative: an orchestrator over three isolated roles.

The broker never touches a settlement rail. Every trade goes through
`Exchange.execute_match`, which records its policy decision before any money
moves — that ordering is the audit trail's guarantee, and routing around it
would silently break the thing this project is judged on.
"""
from __future__ import annotations

from exchange.agents.context import ContextDelta
from exchange.agents.relationships import RelationshipGraph
from exchange.agents.subagents import make_diplomat, make_scout, make_trader
from exchange.agents.subconscious import Subconscious
from exchange.agents.tree import ContextTree
from exchange.ids import new_id
from exchange.matching import find_candidates
from exchange.models import ActorStatus, Currency, Match, Order, Side
from exchange.policy import PolicyContext


class Broker:
    def __init__(
        self,
        actor_id: str,
        exchange,
        provider,
        subconscious: Subconscious | None = None,
        graph: RelationshipGraph | None = None,
    ) -> None:
        self.actor_id = actor_id
        self._exchange = exchange
        self._provider = provider
        self.subconscious = subconscious or Subconscious(provider)
        self.graph = graph or RelationshipGraph()

        self.tree = ContextTree()
        self.root_id = self.tree.add(
            None,
            ContextDelta(objective=f"trade profitably on behalf of {actor_id}"),
            state_version=0,
        )

        version = len(self._exchange.log.read_all())
        self._trader = make_trader(provider, self.tree, self.root_id, version)
        self._scout = make_scout(provider, self.tree, self.root_id, version)
        self._diplomat = make_diplomat(provider, self.tree, self.root_id, version)

    def find_supply(
        self,
        need_text: str,
        qty: int,
        limit_price: int,
        correlation_id: str,
    ) -> list[Match]:
        """Post a descriptive bid and return the candidates worth pursuing."""
        bid = Order(
            order_id=new_id("ord"),
            actor_id=self.actor_id,
            side=Side.BID,
            asset_ref=None,
            asset_query={"text": need_text},
            qty=qty,
            limit_price=limit_price,
            currency=Currency.INR,
            expires_at="2026-12-31T00:00:00+00:00",
            policy_snapshot={},
        )
        self._exchange.post_order(bid, correlation_id=correlation_id)

        state = self._exchange.state()
        asks = [o for o in state.open_orders.values() if o.side == Side.ASK]
        matches = find_candidates(
            bid, asks, state.assets, self._exchange.index,
            counterparty_scores=self.graph.scores(),
        )
        self._trader.act(
            f"We need {qty} of: {need_text}, at no more than {limit_price} each. "
            f"{len(matches)} candidate(s) found."
        )
        return matches

    def assess(self, counterparty_id: str) -> str:
        """Ask the Diplomat about a counterparty, with recall injected first."""
        recalled = self.subconscious.recall(counterparty_id)
        return self._diplomat.act(
            f"What should we know about {counterparty_id} before dealing with them?",
            facts=recalled,
        )

    def close(self, match: Match, seller_id: str, correlation_id: str):
        """Settle through the exchange's gate, then record the relationship."""
        ctx = PolicyContext(
            actor_status=ActorStatus.ACTIVE,
            rolling_spend=0,  # derived from the log inside execute_match
            counterparty_confidence=self.graph.confidence(seller_id),
        )
        decision, settlement = self._exchange.execute_match(
            match, self.actor_id, seller_id, ctx, correlation_id=correlation_id,
        )
        if settlement is not None:
            self.graph.record_deal(
                seller_id,
                value=match.clearing_price * match.qty,
                delivered=True,
            )
        return decision, settlement
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_broker.py -v`
Expected: 8 passed.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/exchange/agents/broker.py tests/test_broker.py
git commit -m "feat: broker orchestrating three isolated sub-agents through the gate"
```

---

### Task 10: Agent reasoning in the audit trail

Spec §6. Without this, the log records that a trade happened but not that an agent
*thought* about it — and Plan 5's replay UI has nothing to show between the bid and the
settlement. Every event carries the trade's `correlation_id`, so reasoning replays
alongside the money rather than in a separate stream.

**Files:**
- Modify: `src/exchange/events.py`
- Create: `src/exchange/agents/journal.py`
- Modify: `src/exchange/agents/broker.py`
- Modify: `src/exchange/agents/negotiation.py`
- Test: `tests/test_journal.py`

**Interfaces:**
- Consumes: `EventLog` from Plan 1; `Lesson` (Task 7); `Offer`, `Outcome` (Task 6).
- Produces:
  - Event constants in `exchange.events`: `RECALL_INJECTED`, `LESSON_CONSOLIDATED`, `NEGOTIATION_OPENED`, `NEGOTIATION_ROUND`, `NEGOTIATION_ENDED`
  - `exchange.agents.journal.AgentJournal(log, actor_id, correlation_id)` with `recall_injected`, `lesson_consolidated`, `negotiation_opened`, `negotiation_round`, `negotiation_ended`
  - `negotiate(..., journal: AgentJournal | None = None)` — journalling is optional so the negotiation stays unit-testable without a log

**`CONTEXT_NODE_CREATED` and `CONTEXT_CHECKPOINT` from the spec are deliberately NOT emitted.** A node is created on every sub-agent action; logging each one would bury the trade's story under bookkeeping, in an append-only log that can never be pruned. The tree is reconstructable from the lessons and negotiation rounds that *are* logged. If Plan 5 turns out to need node-level replay, add them then — that is a cheap addition and an expensive subtraction.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_journal.py`:

```python
import pytest

from exchange.agents.journal import AgentJournal
from exchange.agents.negotiation import negotiate
from exchange.agents.subconscious import Lesson
from exchange.eventlog import EventLog
from exchange.llm.scripted import ScriptedProvider


@pytest.fixture
def log(tmp_path):
    lg = EventLog(str(tmp_path / "journal.db"))
    yield lg
    lg.close()


def test_recall_injection_is_recorded_with_what_was_recalled(log):
    journal = AgentJournal(log, "m_buyer", "c1")

    journal.recall_injected("m_seller", ("pushes on delivery", "pays late"))

    event = log.read_by_correlation("c1")[0]
    assert event.type == "RECALL_INJECTED"
    assert event.payload["counterparty_id"] == "m_seller"
    assert "pushes on delivery" in event.payload["lessons"]


def test_a_consolidated_lesson_is_recorded_with_its_kind(log):
    journal = AgentJournal(log, "m_buyer", "c1")

    journal.lesson_consolidated(Lesson("m_seller", "packaging", "late twice", "reliability"))

    payload = log.read_by_correlation("c1")[0].payload
    assert payload["kind"] == "reliability"
    assert payload["text"] == "late twice"


def test_every_journal_event_carries_the_correlation_id(log):
    journal = AgentJournal(log, "m_buyer", "c_trade_7")

    journal.recall_injected("m_seller", ())
    journal.negotiation_opened("m_seller", 1940)

    assert all(e.correlation_id == "c_trade_7" for e in log.read_all())


def test_negotiation_writes_open_rounds_and_end(log):
    journal = AgentJournal(log, "m_buyer", "c1")

    negotiate("m_buyer", "m_seller",
              ScriptedProvider(["PRICE: 1900 — our offer."]),
              ScriptedProvider(["PRICE: 1900 — agreed."]),
              opening_price=1940, buyer_limit=2200, seller_floor=1800,
              journal=journal)

    types = [e.type for e in log.read_by_correlation("c1")]
    assert types[0] == "NEGOTIATION_OPENED"
    assert types.count("NEGOTIATION_ROUND") == 2
    assert types[-1] == "NEGOTIATION_ENDED"


def test_the_reason_for_ending_is_recorded(log):
    """This is the signal a round counter could never give."""
    journal = AgentJournal(log, "m_buyer", "c1")

    negotiate("m_buyer", "m_seller",
              ScriptedProvider(["WALK: four rupees is not worth another round."]),
              ScriptedProvider(["PRICE: 2100"]),
              opening_price=2100, buyer_limit=2200, seller_floor=1800,
              journal=journal)

    ended = [e for e in log.read_by_correlation("c1") if e.type == "NEGOTIATION_ENDED"][0]
    assert "walked" in ended.payload["reason"]
    assert ended.payload["agreed"] is False


def test_negotiation_works_without_a_journal():
    """Journalling is optional so the negotiation stays unit-testable."""
    outcome = negotiate("m_buyer", "m_seller",
                        ScriptedProvider(["PRICE: 1900"]),
                        ScriptedProvider(["PRICE: 1900"]),
                        opening_price=1940, buyer_limit=2200, seller_floor=1800)

    assert outcome.agreed is True
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_journal.py -v`
Expected: FAIL — no module `exchange.agents.journal`.

- [ ] **Step 3: Add the event constants**

In `src/exchange/events.py`, append to the vocabulary block:

```python
RECALL_INJECTED = "RECALL_INJECTED"
LESSON_CONSOLIDATED = "LESSON_CONSOLIDATED"
NEGOTIATION_OPENED = "NEGOTIATION_OPENED"
NEGOTIATION_ROUND = "NEGOTIATION_ROUND"
NEGOTIATION_ENDED = "NEGOTIATION_ENDED"
```

- [ ] **Step 4: Implement the journal**

Create `src/exchange/agents/journal.py`:

```python
"""Agent reasoning, written into the same log as the money.

A trade's `correlation_id` should reconstruct not just what was bought but
what the agent weighed on the way. These events share that id with the
orders, decisions and settlements, so a replay follows one story rather than
stitching two streams together.
"""
from __future__ import annotations

from exchange import events as ev
from exchange.eventlog import EventLog


class AgentJournal:
    def __init__(self, log: EventLog, actor_id: str, correlation_id: str) -> None:
        self._log = log
        self._actor_id = actor_id
        self._correlation_id = correlation_id

    def _append(self, type: str, payload: dict) -> None:
        self._log.append(
            self._actor_id, type, payload, correlation_id=self._correlation_id
        )

    def recall_injected(self, counterparty_id: str, lessons: tuple[str, ...]) -> None:
        self._append(ev.RECALL_INJECTED, {
            "counterparty_id": counterparty_id,
            "lessons": list(lessons),
        })

    def lesson_consolidated(self, lesson) -> None:
        self._append(ev.LESSON_CONSOLIDATED, {
            "counterparty_id": lesson.counterparty_id,
            "category": lesson.category,
            "text": lesson.text,
            "kind": lesson.kind,
        })

    def negotiation_opened(self, counterparty_id: str, opening_price: int) -> None:
        self._append(ev.NEGOTIATION_OPENED, {
            "counterparty_id": counterparty_id,
            "opening_price": opening_price,
        })

    def negotiation_round(self, actor_id: str, price: int, message: str) -> None:
        self._append(ev.NEGOTIATION_ROUND, {
            "by": actor_id,
            "price": price,
            "message": message,
        })

    def negotiation_ended(self, agreed: bool, final_price: int | None, reason: str) -> None:
        self._append(ev.NEGOTIATION_ENDED, {
            "agreed": agreed,
            "final_price": final_price,
            "reason": reason,
        })
```

- [ ] **Step 5: Wire the journal into negotiation**

In `src/exchange/agents/negotiation.py`, add `journal=None` as the last parameter of `negotiate`, then:

- immediately before the loop: `if journal: journal.negotiation_opened(seller_id, opening_price)`
- after each `offers.append(Offer(actor, price, response.text))`: `if journal: journal.negotiation_round(actor, price, response.text)`
- before each of the four `return Outcome(...)` statements, record the ending. For example, before the agreed return:

```python
        if len(offers) >= 2 and offers[-1].price == offers[-2].price:
            if journal:
                journal.negotiation_ended(True, price, "agreed")
            return Outcome(True, price, tuple(offers), "agreed")
```

Do the same for the walk-away, stalled, and token-budget returns, passing `False`, `None`, and that return's own reason string. Every exit path must journal — a negotiation that ends without a `NEGOTIATION_ENDED` event is a hole in the audit trail.

- [ ] **Step 6: Wire the journal into the broker**

In `src/exchange/agents/broker.py`, add an optional `correlation_id` to `assess` and journal the recall:

```python
    def assess(self, counterparty_id: str, correlation_id: str | None = None) -> str:
        """Ask the Diplomat about a counterparty, with recall injected first."""
        recalled = self.subconscious.recall(counterparty_id)
        if correlation_id and recalled:
            AgentJournal(self._exchange.log, self.actor_id, correlation_id) \
                .recall_injected(counterparty_id, recalled)
        return self._diplomat.act(
            f"What should we know about {counterparty_id} before dealing with them?",
            facts=recalled,
        )
```

Add `from exchange.agents.journal import AgentJournal` to the imports.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/pytest tests/test_journal.py -v`
Expected: 6 passed.

Then `.venv/bin/pytest -q` — all green. Existing `assess` callers pass no `correlation_id` and still work.

- [ ] **Step 8: Commit**

```bash
git add src/exchange/events.py src/exchange/agents/journal.py \
        src/exchange/agents/negotiation.py src/exchange/agents/broker.py \
        tests/test_journal.py
git commit -m "feat: agent reasoning events share the trade's correlation id"
```

---

### Task 11: Two brokers trade, end to end

The acceptance test for this plan, plus a runnable script. **If time runs short, Task 12 goes, not this one.**

**Files:**
- Create: `tests/test_broker_end_to_end.py`
- Create: `scripts/two_brokers.py`

**Interfaces:**
- Consumes: everything.
- Produces: nothing new.

- [ ] **Step 1: Write the end-to-end test**

Create `tests/test_broker_end_to_end.py`:

```python
import pytest

from exchange.agents.broker import Broker
from exchange.agents.context import ContextState
from exchange.agents.negotiation import negotiate
from exchange.eventlog import EventLog
from exchange.llm.scripted import ScriptedProvider
from exchange.models import (
    Actor, ActorKind, Asset, AssetKind, Currency, Order, Side, Verdict,
)
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex
from exchange.service import Exchange
from tests.test_rails import FakeRazorpay
from tests.test_retrieval import fake_embedder

CORR = "corr_two_brokers"


@pytest.fixture
def exchange(tmp_path):
    log = EventLog(str(tmp_path / "e2e.db"))
    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_e2e", "status": "captured"}]}
    })
    ex = Exchange(log, HybridIndex(embed_fn=fake_embedder),
                  RazorpayRail(log, fake, poll_attempts=1, poll_interval=0),
                  CreditRail(log))
    for actor_id in ("m_buyer", "m_seller"):
        ex.register_actor(Actor(actor_id=actor_id, kind=ActorKind.MERCHANT))
    ex.list_asset(Asset(asset_id="ast_mailers", kind=AssetKind.GOODS,
                        title="biodegradable mailers compostable poly", spec={},
                        currency=Currency.INR, origin_actor_id="m_seller"))
    ex.post_order(Order(order_id="ord_ask", actor_id="m_seller", side=Side.ASK,
                        asset_ref="ast_mailers", asset_query=None, qty=1000,
                        limit_price=1940, currency=Currency.INR,
                        expires_at="2026-09-30T00:00:00+00:00", policy_snapshot={}),
                  correlation_id=CORR)
    yield ex
    log.close()


def test_two_brokers_negotiate_and_settle(exchange):
    buyer = Broker("m_buyer", exchange, ScriptedProvider(["candidates found", "advice"]))

    matches = buyer.find_supply("biodegradable compostable mailers", 500, 2200, CORR)
    assert matches

    outcome = negotiate(
        "m_buyer", "m_seller",
        ScriptedProvider(["PRICE: 1900 — that is our offer."]),
        ScriptedProvider(["PRICE: 1900 — agreed."]),
        opening_price=1940, buyer_limit=2200, seller_floor=1800,
    )
    assert outcome.agreed is True

    decision, settlement = buyer.close(matches[0], "m_seller", CORR)

    assert decision.verdict == Verdict.ALLOW
    assert settlement is not None


def test_the_second_deal_is_informed_by_the_first(exchange):
    """The whole point of the Subconscious."""
    provider = ScriptedProvider([
        "candidates found",
        "RELIABILITY: delivered two days late",
        "given the late delivery last time, hold firm on terms",
    ])
    buyer = Broker("m_buyer", exchange, provider)
    buyer.find_supply("biodegradable compostable mailers", 500, 2200, CORR)

    buyer.subconscious.consolidate(
        ContextState(facts=("settled at 1940", "delivery slipped")),
        "m_seller", "packaging",
    )
    advice = buyer.assess("m_seller")

    assert "late delivery" in advice
    assert "delivered two days late" in provider.calls[-1]["messages"][0].content


def test_a_reliability_lesson_lowers_standing_but_never_excludes(exchange):
    buyer = Broker("m_buyer", exchange, ScriptedProvider(["c", "RELIABILITY: no show"]))
    buyer.graph.record_deal("m_seller", value=100_000, delivered=True)
    before = buyer.graph.standing("m_seller")

    lesson = buyer.subconscious.consolidate(
        ContextState(facts=("did not arrive",)), "m_seller", "packaging",
    )
    buyer.graph.apply_lesson(lesson)

    assert buyer.graph.standing("m_seller") < before
    matches = buyer.find_supply("biodegradable compostable mailers", 500, 2200, CORR)
    assert matches, "a poor counterparty must still be offered, only bounded"


def test_the_whole_story_threads_one_correlation_id(exchange):
    buyer = Broker("m_buyer", exchange, ScriptedProvider(["c"]))
    matches = buyer.find_supply("biodegradable compostable mailers", 500, 2200, CORR)
    buyer.close(matches[0], "m_seller", CORR)

    types = [e.type for e in exchange.log.read_by_correlation(CORR)]

    assert "ORDER_POSTED" in types
    assert types.index("MATCH_PROPOSED") < types.index("POLICY_DECIDED")
    assert types.index("POLICY_DECIDED") < types.index("SETTLEMENT_INITIATED")
    assert "ORDER_FILLED" in types
```

- [ ] **Step 2: Run to verify**

Run: `.venv/bin/pytest tests/test_broker_end_to_end.py -v`
Expected: 4 passed. If any fail, fix the underlying module, never the assertion.

- [ ] **Step 3: Write the runnable script**

Create `scripts/two_brokers.py`:

```python
"""Run two brokers against a real LLM provider and print what happened.

  LLM_PROVIDER=ollama  .venv/bin/python scripts/two_brokers.py
  LLM_PROVIDER=deepseek .venv/bin/python scripts/two_brokers.py

Ollama must be running locally for the first; DEEPSEEK_API_KEY must be set
for the second. No Razorpay call is made — this exercises the agents, not
settlement.
"""
from __future__ import annotations

import sys

from dotenv import load_dotenv

from exchange.agents.broker import Broker
from exchange.agents.negotiation import negotiate
from exchange.eventlog import EventLog
from exchange.llm.openai_compat import provider_from_env
from exchange.models import (
    Actor, ActorKind, Asset, AssetKind, Currency, Order, Side,
)
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex, default_embedder
from exchange.service import Exchange
from exchange.ids import new_id


def main() -> int:
    load_dotenv()
    provider = provider_from_env()
    correlation_id = new_id("corr")
    print(f"Correlation id: {correlation_id}\n")

    log = EventLog("runs/brokers.db")
    exchange = Exchange(log, HybridIndex(embed_fn=default_embedder()),
                        RazorpayRail(log, None), CreditRail(log))

    for actor_id in ("m_buyer", "m_seller"):
        exchange.register_actor(Actor(actor_id=actor_id, kind=ActorKind.MERCHANT))
    exchange.list_asset(Asset(
        asset_id="ast_mailers", kind=AssetKind.GOODS,
        title="biodegradable mailers compostable poly 10x13",
        spec={"material": "compostable poly"}, currency=Currency.INR,
        origin_actor_id="m_seller",
    ))
    exchange.post_order(Order(
        order_id="ord_ask", actor_id="m_seller", side=Side.ASK,
        asset_ref="ast_mailers", asset_query=None, qty=1000, limit_price=1940,
        currency=Currency.INR, expires_at="2026-12-31T00:00:00+00:00",
        policy_snapshot={},
    ), correlation_id=correlation_id)

    buyer = Broker("m_buyer", exchange, provider)

    print("=== FINDING SUPPLY ===")
    matches = buyer.find_supply(
        "eco friendly biodegradable mailers under 22 rupees a unit",
        500, 2200, correlation_id,
    )
    if not matches:
        print("No candidates found.")
        return 1
    print(f"{matches[0].rationale}\n")

    print("=== DIPLOMAT ===")
    print(buyer.assess("m_seller") + "\n")

    print("=== NEGOTIATION ===")
    outcome = negotiate("m_buyer", "m_seller", provider, provider,
                        opening_price=1940, buyer_limit=2200, seller_floor=1800)
    for offer in outcome.offers:
        print(f"  {offer.actor_id:>10}: {offer.price:>6}  {offer.message.strip()[:70]}")
    print(f"\n  outcome: {outcome.ended_reason}"
          + (f" at {outcome.final_price}" if outcome.agreed else "") + "\n")

    print("=== AUDIT TRAIL ===")
    for event in log.read_by_correlation(correlation_id):
        print(f"  [{event.seq:>3}] {event.actor_id:<10} {event.type}")

    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run it against Ollama**

```bash
ollama serve &          # if not already running
ollama pull qwen2.5:7b  # ~4.7GB, one time
LLM_PROVIDER=ollama .venv/bin/python scripts/two_brokers.py
```

Expected: a match, Diplomat advice, several negotiation offers, and the audit trail. A small local model may produce a malformed offer — that is exactly what `parse_offer` returning `None` handles, and the negotiation continues. Note in your report whether it happened.

- [ ] **Step 5: Commit**

```bash
git add tests/test_broker_end_to_end.py scripts/two_brokers.py
git commit -m "test: two brokers negotiate and settle end to end"
```

---

### Task 12: Incremental state projection (DROPPABLE)

**Drop this task first if day 6 runs short.** It is worth ~0.2% of runtime today and `fold()` already works correctly. Everything above it matters more.

**Files:**
- Modify: `src/exchange/projections.py`
- Modify: `src/exchange/service.py`
- Test: `tests/test_projections.py`

**Interfaces:**
- Consumes: `fold`, `ExchangeState` from Plan 1.
- Produces:
  - `ExchangeState.event_offset: int`
  - `exchange.projections.fold_from(state: ExchangeState, events: Iterable[Event]) -> ExchangeState`
  - `exchange.service.Exchange.state()` reuses the cached projection when the log has not grown.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_projections.py`:

```python
def test_fold_records_the_offset_it_is_correct_through():
    state = fold([_ev(1, ORDER_POSTED, ORDER_PAYLOAD), _ev(2, ORDER_EXPIRED, {"order_id": "ord_1"})])

    assert state.event_offset == 2


def test_fold_from_applies_only_the_new_events():
    base = fold([_ev(1, ACTOR_REGISTERED, ACTOR_PAYLOAD)])

    grown = fold_from(base, [_ev(2, ORDER_POSTED, ORDER_PAYLOAD)])

    assert "ord_1" in grown.open_orders
    assert grown.event_offset == 2
    assert "m_a" in grown.actors  # inherited, not recomputed


def test_fold_from_equals_a_full_fold():
    """The incremental path must never disagree with the authority."""
    events = [
        _ev(1, ACTOR_REGISTERED, ACTOR_PAYLOAD),
        _ev(2, ORDER_POSTED, ORDER_PAYLOAD),
        _ev(3, CREDITS_TRANSFERRED, {"from_actor_id": "m_a", "to_actor_id": "m_b", "amount": 500}),
    ]

    incremental = fold_from(fold(events[:1]), events[1:])

    assert incremental == fold(events)


def test_fold_from_with_no_new_events_is_unchanged():
    base = fold([_ev(1, ACTOR_REGISTERED, ACTOR_PAYLOAD)])

    assert fold_from(base, []) == base
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_projections.py -v`
Expected: FAIL — `ExchangeState` has no `event_offset`, `fold_from` undefined.

- [ ] **Step 3: Implement**

In `src/exchange/projections.py`, add `event_offset: int = 0` as the last field of `ExchangeState`. Then refactor so `fold` delegates:

```python
def fold(events: Iterable[Event]) -> ExchangeState:
    """Rebuild state from a complete log starting at seq 1.

    This remains the authority. `fold_from` is an optimisation over it and
    must always agree with it — the accountant's job is to prove that.
    """
    return fold_from(ExchangeState(), events)


def fold_from(state: ExchangeState, events: Iterable[Event]) -> ExchangeState:
    """Apply only `events` to an existing state.

    Cost depends on how many events are new, not on how many exist.
    """
    actors = dict(state.actors)
    assets = dict(state.assets)
    open_orders = dict(state.open_orders)
    balances = defaultdict(int, state.credit_balances)
    settlements = dict(state.settlements)
    matches = dict(state.matches)
    offset = state.event_offset

    for event in events:
        offset = max(offset, event.seq)
        # ... the existing per-type branches, unchanged ...

    return ExchangeState(
        actors=actors, assets=assets, open_orders=open_orders,
        credit_balances=dict(balances), settlements=settlements,
        matches=matches, event_offset=offset,
    )
```

Move the existing loop body into `fold_from` verbatim. Do not change any branch.

- [ ] **Step 4: Use it in the service**

In `src/exchange/service.py`, cache the projection on the `Exchange` instance and extend it rather than rebuilding:

```python
    def state(self) -> ExchangeState:
        """Current state, extended from the cached projection.

        The cached value and the log can only disagree if this code is wrong,
        which is exactly what the accountant's periodic full rebuild checks.
        """
        cached = getattr(self, "_state_cache", None)
        if cached is None:
            self._state_cache = fold(self.log.read_all())
        else:
            new_events = self.log.read_since(cached.event_offset)
            if new_events:
                self._state_cache = fold_from(cached, new_events)
        return self._state_cache
```

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all green. Every existing test must pass untouched — that is the proof the incremental path agrees with the authority.

- [ ] **Step 6: Commit**

```bash
git add src/exchange/projections.py src/exchange/service.py tests/test_projections.py
git commit -m "perf: extend the cached projection instead of rebuilding from event 1"
```

---

## Phasing across three days

Ordered so a working broker exists even if the back half slips.

| Day | Tasks | Deliverable |
|---|---|---|
| 4 | 1–4 | Carried defects fixed; provider interface over Ollama and DeepSeek; context state, deltas and the tree |
| 5 | 5–8 | Three isolated sub-agents; negotiation with reasoning backoff; the Subconscious; the relationship graph |
| 6 | 9–12 | The broker; agent reasoning in the audit trail; two brokers end to end; incremental state projection |

**If day 6 slips, drop Task 12 first** — it is worth ~0.2% of runtime today and `fold()`
already works correctly. **Task 10 is not droppable despite looking like plumbing:**
without it the log records that a trade happened but not that an agent reasoned about it,
and Plan 5's replay UI has nothing to show between the bid and the settlement. **Task 7,
the Subconscious, is never droppable** — it is the differentiator, and a broker without
memory is a chatbot with a payment API.

## Plan 2 done when

- `.venv/bin/pytest` is green across every test file.
- `scripts/two_brokers.py` runs against Ollama and produces a match, advice, a negotiation and an audit trail.
- A broker's second assessment of a counterparty visibly reflects the first deal.
- No test makes a network call.

## What Plan 3 builds on

- `Subconscious.consolidate` is called at episode checkpoints — Plan 3's accountant closes those episodes.
- `RelationshipGraph.confidence` feeds `PolicyContext.counterparty_confidence`, which is the trial-size bound.
- `Broker.close` is the only path to money and it already routes through the gate.
- If Task 11 landed, the accountant must also reconcile `Exchange._state_cache` against a full `fold` — that is the drift check the cache's correctness depends on.
