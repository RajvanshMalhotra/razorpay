# Exchange Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the exchange substrate — an append-only event log, a unified order book over two currency rails, hybrid retrieval, a matching engine, and a policy gate — so that a descriptive bid can be matched to an ask, gated, settled through Razorpay test mode, and reconstructed end to end from the log.

**Architecture:** Event-sourced. Every state change appends to a physically append-only SQLite log; all queryable state (order book, balances, relationships) is a fold over that log. Money settles through a `SettlementRail` interface with two implementations — Razorpay test mode for INR, an atomic ledger transfer for CREDITS. Every money action emits a `PolicyDecision` event *before* it executes.

**Tech Stack:** Python 3.11+, SQLite (stdlib `sqlite3`), `razorpay` SDK, `rank_bm25`, `sentence-transformers`, `pytest`, `python-dotenv`.

**Spec:** `docs/superpowers/specs/2026-08-22-agent-exchange-design.md`

## Global Constraints

- Python 3.11 or newer.
- Razorpay **test mode only**. Never wire live keys. Keys live in `.env`, which is gitignored.
- The event log is the single source of truth. All other state is a projection folded from it.
- The log is append-only, enforced by SQLite triggers — no UPDATE, no DELETE.
- Every money action emits a `PolicyDecision` event **before** executing, including when the verdict is `ALLOW`.
- `Asset.kind == INSIGHT` implies `currency == CREDITS`; `GOODS`/`SERVICE` imply `currency == INR`.
- Amounts are integers in minor units (paise for INR, whole points for CREDITS). Never floats.
- `K_MIN = 25` (insight aggregation floor), `MAX_NEGOTIATION_ROUNDS = 4`.
- Points are minted only by the accountant (Plan 3). Nothing in this plan mints points.
- No outreach code. Growth signal is public read-only (Plan 4).
- Every task ends with a commit.

---

### Task 1: Scaffold and Razorpay test-mode reality check

The spec assumes an `order → payment → capture` path. **This task exists to find out what that path actually is on your account before anything is built on top of it.** Razorpay test mode may or may not permit creating a payment purely server-side; Payment Links may be the workable route. Discover it empirically and write the answer down.

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `src/exchange/__init__.py`
- Create: `src/exchange/config.py`
- Create: `scripts/razorpay_probe.py`
- Create: `docs/razorpay-test-mode-findings.md`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `exchange.config.Config` with fields `razorpay_key_id: str`, `razorpay_key_secret: str`, `db_path: str`, `k_min: int`, `max_negotiation_rounds: int`; classmethod `Config.from_env() -> Config`.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "agent-exchange"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "razorpay>=1.4.2",
    "python-dotenv>=1.0.1",
    "rank-bm25>=0.2.2",
    "sentence-transformers>=3.0.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0.0"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: Create the virtualenv and install**

```bash
cd /Users/rajvanshmalhotra/razorpay_project
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Expected: installs cleanly. `sentence-transformers` pulls torch and is slow — let it run.

- [ ] **Step 3: Create `.env.example`**

```bash
# Razorpay TEST MODE keys only. Dashboard -> Settings -> API Keys (Test Mode)
RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx

# Local state
EXCHANGE_DB_PATH=runs/exchange.db
```

Then copy it and fill in real test keys:

```bash
cp .env.example .env
```

- [ ] **Step 4: Write the failing test for `Config`**

Create `tests/test_config.py`:

```python
import pytest
from exchange.config import Config


def test_from_env_reads_values(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret123")
    monkeypatch.setenv("EXCHANGE_DB_PATH", "runs/test.db")

    cfg = Config.from_env()

    assert cfg.razorpay_key_id == "rzp_test_abc"
    assert cfg.razorpay_key_secret == "secret123"
    assert cfg.db_path == "runs/test.db"
    assert cfg.k_min == 25
    assert cfg.max_negotiation_rounds == 4


def test_from_env_rejects_live_keys(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret123")

    with pytest.raises(ValueError, match="test mode"):
        Config.from_env()


def test_from_env_requires_key_id(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret123")

    with pytest.raises(ValueError, match="RAZORPAY_KEY_ID"):
        Config.from_env()
```

- [ ] **Step 5: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'exchange.config'`

- [ ] **Step 6: Implement `Config`**

Create `src/exchange/__init__.py` as an empty file, then `src/exchange/config.py`:

```python
"""Configuration loaded from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

K_MIN = 25
MAX_NEGOTIATION_ROUNDS = 4


@dataclass(frozen=True)
class Config:
    razorpay_key_id: str
    razorpay_key_secret: str
    db_path: str
    k_min: int = K_MIN
    max_negotiation_rounds: int = MAX_NEGOTIATION_ROUNDS

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()

        key_id = os.environ.get("RAZORPAY_KEY_ID", "")
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")

        if not key_id:
            raise ValueError("RAZORPAY_KEY_ID is not set")
        if not key_secret:
            raise ValueError("RAZORPAY_KEY_SECRET is not set")
        if not key_id.startswith("rzp_test_"):
            raise ValueError(
                f"Refusing to run: key {key_id[:12]}... is not a test mode key. "
                "This project runs in test mode only."
            )

        return cls(
            razorpay_key_id=key_id,
            razorpay_key_secret=key_secret,
            db_path=os.environ.get("EXCHANGE_DB_PATH", "runs/exchange.db"),
        )
```

Create an empty `tests/__init__.py`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: 3 passed

- [ ] **Step 8: Write the Razorpay probe script**

Create `scripts/razorpay_probe.py`:

```python
"""Probe what the Razorpay test-mode account actually permits.

Run this before building anything on the INR rail. It answers one question:
how does a payment get created and captured without a browser checkout?
"""
from __future__ import annotations

import json
import sys

import razorpay

from exchange.config import Config


def main() -> int:
    cfg = Config.from_env()
    client = razorpay.Client(auth=(cfg.razorpay_key_id, cfg.razorpay_key_secret))

    print("=== 1. Create an order ===")
    order = client.order.create({
        "amount": 970000,          # paise; 500 units @ 19.40
        "currency": "INR",
        "receipt": "probe_receipt_1",
        "notes": {"probe": "exchange-core-task-1"},
    })
    print(json.dumps(order, indent=2))
    order_id = order["id"]

    print("\n=== 2. Fetch payments for that order ===")
    payments = client.order.payments(order_id)
    print(json.dumps(payments, indent=2))
    print("If count == 0, no payment exists yet and one cannot be willed "
          "into existence server-side.")

    print("\n=== 3. Try a payment link ===")
    try:
        link = client.payment_link.create({
            "amount": 970000,
            "currency": "INR",
            "description": "Exchange core probe",
            "notes": {"probe": "exchange-core-task-1"},
        })
        print(json.dumps(link, indent=2))
        print("\nOpen short_url in a browser, pay with test card "
              "4111 1111 1111 1111, any future expiry, any CVV.")
    except Exception as exc:  # noqa: BLE001 - probe script, report anything
        print(f"payment_link.create failed: {type(exc).__name__}: {exc}")

    print("\n=== 4. Order state after ===")
    print(json.dumps(client.order.fetch(order_id), indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 9: Run the probe and record what happened**

Run: `.venv/bin/python scripts/razorpay_probe.py`

Then open the printed `short_url`, pay with test card `4111 1111 1111 1111`, and re-run steps 2 and 4 by running the probe's fetch calls again (or just re-run the script and note the new order's behaviour versus the paid one).

- [ ] **Step 10: Write `docs/razorpay-test-mode-findings.md`**

Record the answers, verbatim from what you observed. Do not guess:

```markdown
# Razorpay test-mode findings

Probed on: <date>
Key id prefix: rzp_test_...

## Can an order be created server-side?
<yes/no, with the response shape>

## Can a payment be created without a browser?
<yes/no — what happened>

## Does payment_link.create work on this account?
<yes/no — the error if not>

## What is the working path to a captured payment?
<the concrete sequence that worked>

## Implication for the INR rail
<which mechanism `RazorpayRail.settle()` will use in Task 8>

## Sample IDs captured
order_id: ...
payment_id: ...
```

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml .env.example src/exchange/__init__.py src/exchange/config.py \
        scripts/razorpay_probe.py docs/razorpay-test-mode-findings.md \
        tests/__init__.py tests/test_config.py
git commit -m "feat: scaffold project and probe Razorpay test-mode capabilities"
```

---

### Task 2: Append-only event log

**Files:**
- Create: `src/exchange/ids.py`
- Create: `src/exchange/events.py`
- Create: `src/exchange/eventlog.py`
- Test: `tests/test_eventlog.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `exchange.ids.new_id(prefix: str) -> str`
  - `exchange.events.Event` — frozen dataclass with `event_id: str, seq: int, ts: str, actor_id: str, type: str, payload: dict, causation_id: str | None, correlation_id: str`
  - `exchange.eventlog.EventLog(db_path: str)` with:
    - `append(actor_id: str, type: str, payload: dict, correlation_id: str, causation_id: str | None = None) -> Event`
    - `read_all() -> list[Event]`
    - `read_by_correlation(correlation_id: str) -> list[Event]`
    - `read_since(seq: int) -> list[Event]`
    - `close() -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_eventlog.py`:

```python
import sqlite3

import pytest

from exchange.eventlog import EventLog


@pytest.fixture
def log(tmp_path):
    lg = EventLog(str(tmp_path / "test.db"))
    yield lg
    lg.close()


def test_append_returns_event_with_seq(log):
    ev = log.append("merchant_a", "ORDER_POSTED", {"qty": 500}, correlation_id="corr_1")

    assert ev.seq == 1
    assert ev.actor_id == "merchant_a"
    assert ev.type == "ORDER_POSTED"
    assert ev.payload == {"qty": 500}
    assert ev.correlation_id == "corr_1"
    assert ev.causation_id is None
    assert ev.event_id.startswith("evt_")


def test_seq_increments_monotonically(log):
    a = log.append("m", "A", {}, correlation_id="c")
    b = log.append("m", "B", {}, correlation_id="c")
    c = log.append("m", "C", {}, correlation_id="c")

    assert [a.seq, b.seq, c.seq] == [1, 2, 3]


def test_read_by_correlation_filters_and_orders(log):
    log.append("m", "A", {}, correlation_id="corr_1")
    log.append("m", "B", {}, correlation_id="corr_2")
    log.append("m", "C", {}, correlation_id="corr_1")

    events = log.read_by_correlation("corr_1")

    assert [e.type for e in events] == ["A", "C"]


def test_read_since_excludes_given_seq(log):
    log.append("m", "A", {}, correlation_id="c")
    log.append("m", "B", {}, correlation_id="c")
    log.append("m", "C", {}, correlation_id="c")

    events = log.read_since(1)

    assert [e.type for e in events] == ["B", "C"]


def test_causation_is_recorded(log):
    first = log.append("m", "A", {}, correlation_id="c")
    second = log.append("m", "B", {}, correlation_id="c", causation_id=first.event_id)

    assert second.causation_id == first.event_id


def test_payload_roundtrips_nested_structures(log):
    payload = {"terms": {"price": 1940, "qty": 500}, "tags": ["eco", "urgent"]}
    log.append("m", "A", payload, correlation_id="c")

    assert log.read_all()[0].payload == payload


def test_update_is_physically_blocked(log):
    log.append("m", "A", {}, correlation_id="c")

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        log._conn.execute("UPDATE events SET type = 'TAMPERED' WHERE seq = 1")


def test_delete_is_physically_blocked(log):
    log.append("m", "A", {}, correlation_id="c")

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        log._conn.execute("DELETE FROM events WHERE seq = 1")


def test_reopening_the_log_preserves_history(tmp_path):
    path = str(tmp_path / "persist.db")
    first = EventLog(path)
    first.append("m", "A", {}, correlation_id="c")
    first.close()

    second = EventLog(path)
    try:
        assert [e.type for e in second.read_all()] == ["A"]
        assert second.append("m", "B", {}, correlation_id="c").seq == 2
    finally:
        second.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_eventlog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'exchange.eventlog'`

- [ ] **Step 3: Implement `ids.py`**

Create `src/exchange/ids.py`:

```python
"""Prefixed identifier generation."""
from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    """Return an id like 'evt_9f3c1a2b4d5e'."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
```

- [ ] **Step 4: Implement `events.py`**

Create `src/exchange/events.py`:

```python
"""The event record and the vocabulary of event types."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Event:
    event_id: str
    seq: int
    ts: str
    actor_id: str
    type: str
    payload: dict[str, Any]
    causation_id: str | None
    correlation_id: str


# Event type vocabulary. Kept as constants so typos fail at import, not at runtime.
ACTOR_REGISTERED = "ACTOR_REGISTERED"
ASSET_LISTED = "ASSET_LISTED"
ORDER_POSTED = "ORDER_POSTED"
ORDER_EXPIRED = "ORDER_EXPIRED"
MATCH_PROPOSED = "MATCH_PROPOSED"
POLICY_DECIDED = "POLICY_DECIDED"
SETTLEMENT_INITIATED = "SETTLEMENT_INITIATED"
SETTLEMENT_COMPLETED = "SETTLEMENT_COMPLETED"
SETTLEMENT_FAILED = "SETTLEMENT_FAILED"
CREDITS_TRANSFERRED = "CREDITS_TRANSFERRED"
```

- [ ] **Step 5: Implement `eventlog.py`**

Create `src/exchange/eventlog.py`:

```python
"""Append-only event log over SQLite.

The log is the single source of truth. Append-only is enforced by triggers,
not by convention — an UPDATE or DELETE raises at the database level.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from exchange.events import Event
from exchange.ids import new_id

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL UNIQUE,
    ts              TEXT NOT NULL,
    actor_id        TEXT NOT NULL,
    type            TEXT NOT NULL,
    payload         TEXT NOT NULL,
    causation_id    TEXT,
    correlation_id  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_correlation ON events(correlation_id);
CREATE INDEX IF NOT EXISTS idx_events_actor ON events(actor_id);

CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events is append-only: UPDATE is not permitted');
END;

CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events is append-only: DELETE is not permitted');
END;
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventLog:
    def __init__(self, db_path: str) -> None:
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def append(
        self,
        actor_id: str,
        type: str,
        payload: dict[str, Any],
        correlation_id: str,
        causation_id: str | None = None,
    ) -> Event:
        event_id = new_id("evt")
        ts = _utc_now()
        cursor = self._conn.execute(
            "INSERT INTO events (event_id, ts, actor_id, type, payload, "
            "causation_id, correlation_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, ts, actor_id, type, json.dumps(payload), causation_id, correlation_id),
        )
        self._conn.commit()
        return Event(
            event_id=event_id,
            seq=cursor.lastrowid,
            ts=ts,
            actor_id=actor_id,
            type=type,
            payload=payload,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )

    def read_all(self) -> list[Event]:
        return self._query("SELECT * FROM events ORDER BY seq", ())

    def read_by_correlation(self, correlation_id: str) -> list[Event]:
        return self._query(
            "SELECT * FROM events WHERE correlation_id = ? ORDER BY seq",
            (correlation_id,),
        )

    def read_since(self, seq: int) -> list[Event]:
        return self._query("SELECT * FROM events WHERE seq > ? ORDER BY seq", (seq,))

    def close(self) -> None:
        self._conn.close()

    def _query(self, sql: str, params: tuple) -> list[Event]:
        rows = self._conn.execute(sql, params).fetchall()
        return [
            Event(
                event_id=r["event_id"],
                seq=r["seq"],
                ts=r["ts"],
                actor_id=r["actor_id"],
                type=r["type"],
                payload=json.loads(r["payload"]),
                causation_id=r["causation_id"],
                correlation_id=r["correlation_id"],
            )
            for r in rows
        ]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_eventlog.py -v`
Expected: 9 passed

- [ ] **Step 7: Commit**

```bash
git add src/exchange/ids.py src/exchange/events.py src/exchange/eventlog.py tests/test_eventlog.py
git commit -m "feat: append-only event log with trigger-enforced immutability"
```

---

### Task 3: Domain models

**Files:**
- Create: `src/exchange/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces, all in `exchange.models`:
  - Enums (str-valued): `ActorKind`, `ActorStatus`, `AssetKind`, `Currency`, `Side`, `Verdict`, `SettlementStatus`
  - Frozen dataclasses `Actor`, `Asset`, `Order`, `Match`, `Settlement`, `PolicyDecision`, `CreditLedgerEntry`, `RelationshipEdge`
  - `Order.is_descriptive` property returning `bool`
  - `currency_for_kind(kind: AssetKind) -> Currency`
  - Module raises `ValueError` on construction of an `Asset` whose `kind`/`currency` pair is invalid

- [ ] **Step 1: Write the failing tests**

Create `tests/test_models.py`:

```python
import pytest

from exchange.models import (
    Asset,
    AssetKind,
    Currency,
    Order,
    Side,
    currency_for_kind,
)


def test_currency_for_kind_maps_goods_and_services_to_inr():
    assert currency_for_kind(AssetKind.GOODS) == Currency.INR
    assert currency_for_kind(AssetKind.SERVICE) == Currency.INR


def test_currency_for_kind_maps_insight_to_credits():
    assert currency_for_kind(AssetKind.INSIGHT) == Currency.CREDITS


def test_asset_rejects_insight_priced_in_inr():
    with pytest.raises(ValueError, match="INSIGHT"):
        Asset(
            asset_id="ast_1",
            kind=AssetKind.INSIGHT,
            title="Skincare AOV up 12%",
            spec={},
            currency=Currency.INR,
            origin_actor_id="house",
        )


def test_asset_rejects_goods_priced_in_credits():
    with pytest.raises(ValueError, match="GOODS"):
        Asset(
            asset_id="ast_1",
            kind=AssetKind.GOODS,
            title="Corrugated boxes",
            spec={},
            currency=Currency.CREDITS,
            origin_actor_id="merchant_a",
        )


def test_asset_accepts_valid_pairing():
    asset = Asset(
        asset_id="ast_1",
        kind=AssetKind.GOODS,
        title="Corrugated boxes",
        spec={"material": "kraft"},
        currency=Currency.INR,
        origin_actor_id="merchant_a",
    )
    assert asset.currency == Currency.INR


def test_order_is_descriptive_when_it_carries_a_query():
    order = Order(
        order_id="ord_1",
        actor_id="merchant_a",
        side=Side.BID,
        asset_ref=None,
        asset_query={"text": "eco packaging", "max_unit_price": 2200},
        qty=500,
        limit_price=1100000,
        currency=Currency.INR,
        expires_at="2026-09-01T00:00:00+00:00",
        policy_snapshot={},
    )
    assert order.is_descriptive is True


def test_order_is_not_descriptive_when_it_references_an_asset():
    order = Order(
        order_id="ord_1",
        actor_id="merchant_a",
        side=Side.BID,
        asset_ref="ast_1",
        asset_query=None,
        qty=500,
        limit_price=1100000,
        currency=Currency.INR,
        expires_at="2026-09-01T00:00:00+00:00",
        policy_snapshot={},
    )
    assert order.is_descriptive is False


def test_order_rejects_both_ref_and_query():
    with pytest.raises(ValueError, match="exactly one"):
        Order(
            order_id="ord_1",
            actor_id="merchant_a",
            side=Side.BID,
            asset_ref="ast_1",
            asset_query={"text": "eco packaging"},
            qty=500,
            limit_price=1100000,
            currency=Currency.INR,
            expires_at="2026-09-01T00:00:00+00:00",
            policy_snapshot={},
        )


def test_order_rejects_neither_ref_nor_query():
    with pytest.raises(ValueError, match="exactly one"):
        Order(
            order_id="ord_1",
            actor_id="merchant_a",
            side=Side.BID,
            asset_ref=None,
            asset_query=None,
            qty=500,
            limit_price=1100000,
            currency=Currency.INR,
            expires_at="2026-09-01T00:00:00+00:00",
            policy_snapshot={},
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'exchange.models'`

- [ ] **Step 3: Implement `models.py`**

Create `src/exchange/models.py`:

```python
"""Domain records. All amounts are integers in minor units — never floats."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ActorKind(StrEnum):
    MERCHANT = "MERCHANT"
    HOUSE = "HOUSE"
    ACCOUNTANT = "ACCOUNTANT"
    HUMAN = "HUMAN"


class ActorStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"


class AssetKind(StrEnum):
    GOODS = "GOODS"
    SERVICE = "SERVICE"
    INSIGHT = "INSIGHT"


class Currency(StrEnum):
    INR = "INR"
    CREDITS = "CREDITS"


class Side(StrEnum):
    BID = "BID"
    ASK = "ASK"


class Verdict(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"


class SettlementStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def currency_for_kind(kind: AssetKind) -> Currency:
    """INSIGHT trades in points; everything physical trades in rupees."""
    return Currency.CREDITS if kind == AssetKind.INSIGHT else Currency.INR


@dataclass(frozen=True)
class Actor:
    actor_id: str
    kind: ActorKind
    merchant_id: str | None = None
    plan_tier: str = "standard"
    status: ActorStatus = ActorStatus.ACTIVE


@dataclass(frozen=True)
class Asset:
    asset_id: str
    kind: AssetKind
    title: str
    spec: dict[str, Any]
    currency: Currency
    origin_actor_id: str

    def __post_init__(self) -> None:
        expected = currency_for_kind(self.kind)
        if self.currency != expected:
            raise ValueError(
                f"{self.kind} assets must be priced in {expected}, got {self.currency}"
            )


@dataclass(frozen=True)
class Order:
    order_id: str
    actor_id: str
    side: Side
    asset_ref: str | None
    asset_query: dict[str, Any] | None
    qty: int
    limit_price: int
    currency: Currency
    expires_at: str
    policy_snapshot: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.asset_ref is None) == (self.asset_query is None):
            raise ValueError(
                "An order must carry exactly one of asset_ref or asset_query"
            )

    @property
    def is_descriptive(self) -> bool:
        return self.asset_query is not None


@dataclass(frozen=True)
class Match:
    match_id: str
    bid_order_id: str
    ask_order_id: str
    clearing_price: int
    score: float
    rationale: str


@dataclass(frozen=True)
class Settlement:
    settlement_id: str
    match_id: str
    currency: Currency
    amount: int
    status: SettlementStatus
    razorpay_order_id: str | None = None
    razorpay_payment_id: str | None = None
    reconciled_at: str | None = None
    reconciliation_status: str | None = None


@dataclass(frozen=True)
class PolicyDecision:
    decision_id: str
    action_ref: str
    actor_id: str
    verdict: Verdict
    reason: str
    limits_evaluated: dict[str, Any]
    ts: str


@dataclass(frozen=True)
class CreditLedgerEntry:
    entry_id: str
    actor_id: str
    delta: int
    reason: str
    source_settlement_id: str | None
    ts: str


@dataclass(frozen=True)
class RelationshipEdge:
    from_actor_id: str
    to_actor_id: str
    deals_count: int = 0
    total_value: int = 0
    reliability_score: float = 0.5
    confidence: float = 0.0
    last_interaction_at: str | None = None
    lessons_ref: str | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/exchange/models.py tests/test_models.py
git commit -m "feat: domain models with kind/currency and order-shape invariants"
```

---

### Task 4: Projections — folding the log into state

**Files:**
- Create: `src/exchange/projections.py`
- Test: `tests/test_projections.py`

**Interfaces:**
- Consumes: `exchange.events.Event`, the event type constants, `exchange.models`.
- Produces: `exchange.projections.ExchangeState` — a frozen dataclass with `actors: dict[str, Actor]`, `assets: dict[str, Asset]`, `open_orders: dict[str, Order]`, `credit_balances: dict[str, int]`, `settlements: dict[str, Settlement]` — and `fold(events: Iterable[Event]) -> ExchangeState`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_projections.py`:

```python
from exchange.events import (
    ACTOR_REGISTERED,
    ASSET_LISTED,
    CREDITS_TRANSFERRED,
    ORDER_EXPIRED,
    ORDER_POSTED,
    SETTLEMENT_COMPLETED,
    SETTLEMENT_INITIATED,
    Event,
)
from exchange.models import ActorStatus, Currency, SettlementStatus, Side
from exchange.projections import fold


def _ev(seq, type, payload, actor_id="m_a", correlation_id="c"):
    return Event(
        event_id=f"evt_{seq}",
        seq=seq,
        ts="2026-08-22T00:00:00+00:00",
        actor_id=actor_id,
        type=type,
        payload=payload,
        causation_id=None,
        correlation_id=correlation_id,
    )


ACTOR_PAYLOAD = {
    "actor_id": "m_a",
    "kind": "MERCHANT",
    "merchant_id": "acc_1",
    "plan_tier": "standard",
    "status": "ACTIVE",
}

ORDER_PAYLOAD = {
    "order_id": "ord_1",
    "actor_id": "m_a",
    "side": "BID",
    "asset_ref": None,
    "asset_query": {"text": "eco packaging"},
    "qty": 500,
    "limit_price": 1100000,
    "currency": "INR",
    "expires_at": "2026-09-01T00:00:00+00:00",
    "policy_snapshot": {},
}


def test_fold_of_empty_log_is_empty():
    state = fold([])

    assert state.actors == {}
    assert state.open_orders == {}
    assert state.credit_balances == {}


def test_actor_registered_appears_in_state():
    state = fold([_ev(1, ACTOR_REGISTERED, ACTOR_PAYLOAD)])

    assert state.actors["m_a"].status == ActorStatus.ACTIVE


def test_order_posted_enters_the_book():
    state = fold([_ev(1, ORDER_POSTED, ORDER_PAYLOAD)])

    order = state.open_orders["ord_1"]
    assert order.side == Side.BID
    assert order.qty == 500
    assert order.is_descriptive is True


def test_order_expired_leaves_the_book():
    state = fold([
        _ev(1, ORDER_POSTED, ORDER_PAYLOAD),
        _ev(2, ORDER_EXPIRED, {"order_id": "ord_1"}),
    ])

    assert "ord_1" not in state.open_orders


def test_asset_listed_appears_in_state():
    state = fold([
        _ev(1, ASSET_LISTED, {
            "asset_id": "ast_1",
            "kind": "GOODS",
            "title": "Corrugated boxes",
            "spec": {"material": "kraft"},
            "currency": "INR",
            "origin_actor_id": "m_b",
        })
    ])

    assert state.assets["ast_1"].title == "Corrugated boxes"


def test_credits_transferred_moves_balance_both_ways():
    state = fold([
        _ev(1, CREDITS_TRANSFERRED, {"from_actor_id": "m_a", "to_actor_id": "m_b", "amount": 1200}),
    ])

    assert state.credit_balances["m_a"] == -1200
    assert state.credit_balances["m_b"] == 1200


def test_credits_are_conserved_across_many_transfers():
    events = [
        _ev(1, CREDITS_TRANSFERRED, {"from_actor_id": "m_a", "to_actor_id": "m_b", "amount": 500}),
        _ev(2, CREDITS_TRANSFERRED, {"from_actor_id": "m_b", "to_actor_id": "m_c", "amount": 200}),
        _ev(3, CREDITS_TRANSFERRED, {"from_actor_id": "m_c", "to_actor_id": "m_a", "amount": 50}),
    ]

    state = fold(events)

    assert sum(state.credit_balances.values()) == 0


def test_settlement_transitions_from_pending_to_completed():
    state = fold([
        _ev(1, SETTLEMENT_INITIATED, {
            "settlement_id": "stl_1",
            "match_id": "mch_1",
            "currency": "INR",
            "amount": 970000,
            "razorpay_order_id": "order_abc",
        }),
        _ev(2, SETTLEMENT_COMPLETED, {
            "settlement_id": "stl_1",
            "razorpay_payment_id": "pay_xyz",
        }),
    ])

    stl = state.settlements["stl_1"]
    assert stl.status == SettlementStatus.COMPLETED
    assert stl.razorpay_payment_id == "pay_xyz"
    assert stl.currency == Currency.INR


def test_fold_is_deterministic_for_the_same_events():
    events = [_ev(1, ACTOR_REGISTERED, ACTOR_PAYLOAD), _ev(2, ORDER_POSTED, ORDER_PAYLOAD)]

    assert fold(events) == fold(events)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_projections.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'exchange.projections'`

- [ ] **Step 3: Implement `projections.py`**

Create `src/exchange/projections.py`:

```python
"""Fold the event log into queryable state.

Nothing here holds state of its own. Every value is derived from the log,
so the log and the state can never disagree.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Iterable

from exchange import events as ev
from exchange.events import Event
from exchange.models import (
    Actor,
    ActorKind,
    ActorStatus,
    Asset,
    AssetKind,
    Currency,
    Order,
    Settlement,
    SettlementStatus,
    Side,
)


@dataclass(frozen=True)
class ExchangeState:
    actors: dict[str, Actor] = field(default_factory=dict)
    assets: dict[str, Asset] = field(default_factory=dict)
    open_orders: dict[str, Order] = field(default_factory=dict)
    credit_balances: dict[str, int] = field(default_factory=dict)
    settlements: dict[str, Settlement] = field(default_factory=dict)


def fold(events: Iterable[Event]) -> ExchangeState:
    actors: dict[str, Actor] = {}
    assets: dict[str, Asset] = {}
    open_orders: dict[str, Order] = {}
    balances: dict[str, int] = defaultdict(int)
    settlements: dict[str, Settlement] = {}

    for event in events:
        p = event.payload

        if event.type == ev.ACTOR_REGISTERED:
            actors[p["actor_id"]] = Actor(
                actor_id=p["actor_id"],
                kind=ActorKind(p["kind"]),
                merchant_id=p.get("merchant_id"),
                plan_tier=p.get("plan_tier", "standard"),
                status=ActorStatus(p.get("status", "ACTIVE")),
            )

        elif event.type == ev.ASSET_LISTED:
            assets[p["asset_id"]] = Asset(
                asset_id=p["asset_id"],
                kind=AssetKind(p["kind"]),
                title=p["title"],
                spec=p.get("spec", {}),
                currency=Currency(p["currency"]),
                origin_actor_id=p["origin_actor_id"],
            )

        elif event.type == ev.ORDER_POSTED:
            open_orders[p["order_id"]] = Order(
                order_id=p["order_id"],
                actor_id=p["actor_id"],
                side=Side(p["side"]),
                asset_ref=p.get("asset_ref"),
                asset_query=p.get("asset_query"),
                qty=p["qty"],
                limit_price=p["limit_price"],
                currency=Currency(p["currency"]),
                expires_at=p["expires_at"],
                policy_snapshot=p.get("policy_snapshot", {}),
            )

        elif event.type == ev.ORDER_EXPIRED:
            open_orders.pop(p["order_id"], None)

        elif event.type == ev.CREDITS_TRANSFERRED:
            balances[p["from_actor_id"]] -= p["amount"]
            balances[p["to_actor_id"]] += p["amount"]

        elif event.type == ev.SETTLEMENT_INITIATED:
            settlements[p["settlement_id"]] = Settlement(
                settlement_id=p["settlement_id"],
                match_id=p["match_id"],
                currency=Currency(p["currency"]),
                amount=p["amount"],
                status=SettlementStatus.PENDING,
                razorpay_order_id=p.get("razorpay_order_id"),
            )

        elif event.type == ev.SETTLEMENT_COMPLETED:
            existing = settlements[p["settlement_id"]]
            settlements[p["settlement_id"]] = replace(
                existing,
                status=SettlementStatus.COMPLETED,
                razorpay_payment_id=p.get("razorpay_payment_id"),
            )

        elif event.type == ev.SETTLEMENT_FAILED:
            existing = settlements[p["settlement_id"]]
            settlements[p["settlement_id"]] = replace(
                existing, status=SettlementStatus.FAILED
            )

    return ExchangeState(
        actors=actors,
        assets=assets,
        open_orders=open_orders,
        credit_balances=dict(balances),
        settlements=settlements,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_projections.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/exchange/projections.py tests/test_projections.py
git commit -m "feat: fold event log into exchange state projections"
```

---

### Task 5: The policy gate

**Files:**
- Create: `src/exchange/policy.py`
- Test: `tests/test_policy.py`

**Interfaces:**
- Consumes: `exchange.models.{PolicyDecision, Verdict, Currency, ActorStatus}`, `exchange.ids.new_id`.
- Produces:
  - `exchange.policy.PolicyLimits` — frozen dataclass: `per_txn_cap: int`, `rolling_window_cap: int`, `human_approval_threshold: int`, `unknown_counterparty_cap: int`, `confidence_floor: float` (default `0.3`)
  - `exchange.policy.PolicyContext` — frozen dataclass: `actor_status: ActorStatus`, `rolling_spend: int`, `counterparty_confidence: float`
  - `exchange.policy.evaluate(action_ref: str, actor_id: str, amount: int, currency: Currency, ctx: PolicyContext, limits: PolicyLimits) -> PolicyDecision`
  - `exchange.policy.DEFAULT_INR_LIMITS`, `exchange.policy.DEFAULT_CREDIT_LIMITS`

Precedence, highest first: frozen actor → per-transaction cap → rolling window cap → unknown-counterparty cap → human approval threshold → allow. A `DENY` always beats a `REQUIRE_HUMAN`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_policy.py`:

```python
from exchange.models import ActorStatus, Currency, Verdict
from exchange.policy import PolicyContext, PolicyLimits, evaluate

LIMITS = PolicyLimits(
    per_txn_cap=1_000_000,
    rolling_window_cap=5_000_000,
    human_approval_threshold=800_000,
    unknown_counterparty_cap=500_000,
    confidence_floor=0.3,
)

TRUSTED = PolicyContext(
    actor_status=ActorStatus.ACTIVE, rolling_spend=0, counterparty_confidence=0.9
)


def _evaluate(amount, ctx=TRUSTED, limits=LIMITS):
    return evaluate("mch_1", "m_a", amount, Currency.INR, ctx, limits)


def test_small_trade_with_a_trusted_counterparty_is_allowed():
    decision = _evaluate(100_000)

    assert decision.verdict == Verdict.ALLOW
    assert decision.actor_id == "m_a"
    assert decision.action_ref == "mch_1"


def test_decision_always_records_the_limits_it_evaluated():
    decision = _evaluate(100_000)

    assert decision.limits_evaluated["per_txn_cap"] == 1_000_000
    assert decision.limits_evaluated["amount"] == 100_000


def test_frozen_actor_is_denied():
    ctx = PolicyContext(
        actor_status=ActorStatus.FROZEN, rolling_spend=0, counterparty_confidence=0.9
    )

    decision = _evaluate(1_000, ctx)

    assert decision.verdict == Verdict.DENY
    assert "frozen" in decision.reason.lower()


def test_amount_over_per_txn_cap_is_denied():
    decision = _evaluate(1_000_001)

    assert decision.verdict == Verdict.DENY
    assert "per-transaction" in decision.reason


def test_amount_that_breaches_rolling_window_is_denied():
    ctx = PolicyContext(
        actor_status=ActorStatus.ACTIVE,
        rolling_spend=4_900_000,
        counterparty_confidence=0.9,
    )

    decision = _evaluate(200_000, ctx)

    assert decision.verdict == Verdict.DENY
    assert "rolling" in decision.reason


def test_unknown_counterparty_is_capped_low():
    unknown = PolicyContext(
        actor_status=ActorStatus.ACTIVE, rolling_spend=0, counterparty_confidence=0.05
    )

    decision = _evaluate(600_000, unknown)

    assert decision.verdict == Verdict.DENY
    assert "unknown counterparty" in decision.reason


def test_unknown_counterparty_may_still_trade_small():
    unknown = PolicyContext(
        actor_status=ActorStatus.ACTIVE, rolling_spend=0, counterparty_confidence=0.05
    )

    decision = _evaluate(400_000, unknown)

    assert decision.verdict == Verdict.ALLOW


def test_large_trade_requires_human_approval():
    decision = _evaluate(900_000)

    assert decision.verdict == Verdict.REQUIRE_HUMAN
    assert "human" in decision.reason.lower()


def test_deny_beats_require_human():
    """Over both the human threshold and the per-txn cap: DENY wins."""
    decision = _evaluate(2_000_000)

    assert decision.verdict == Verdict.DENY


def test_decision_ids_are_unique():
    a = _evaluate(1_000)
    b = _evaluate(1_000)

    assert a.decision_id != b.decision_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'exchange.policy'`

- [ ] **Step 3: Implement `policy.py`**

Create `src/exchange/policy.py`:

```python
"""The policy gate.

Every money action is evaluated here first, and the decision is logged whether
the answer is yes or no. Explainability is the point: a decision always carries
the reason and the limits it weighed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from exchange.ids import new_id
from exchange.models import ActorStatus, Currency, PolicyDecision, Verdict


@dataclass(frozen=True)
class PolicyLimits:
    per_txn_cap: int
    rolling_window_cap: int
    human_approval_threshold: int
    unknown_counterparty_cap: int
    confidence_floor: float = 0.3


@dataclass(frozen=True)
class PolicyContext:
    actor_status: ActorStatus
    rolling_spend: int
    counterparty_confidence: float


# Rupee amounts in paise.
DEFAULT_INR_LIMITS = PolicyLimits(
    per_txn_cap=2_000_000,            # Rs 20,000
    rolling_window_cap=10_000_000,    # Rs 1,00,000
    human_approval_threshold=1_500_000,  # Rs 15,000
    unknown_counterparty_cap=500_000,    # Rs 5,000 — the trial-size bound
)

# Points. Deliberately looser: this rail cannot cause real harm.
DEFAULT_CREDIT_LIMITS = PolicyLimits(
    per_txn_cap=50_000,
    rolling_window_cap=200_000,
    human_approval_threshold=1_000_000_000,  # effectively never
    unknown_counterparty_cap=50_000,
)


def evaluate(
    action_ref: str,
    actor_id: str,
    amount: int,
    currency: Currency,
    ctx: PolicyContext,
    limits: PolicyLimits,
) -> PolicyDecision:
    """Decide whether a money action may proceed.

    Precedence, highest first: frozen actor, per-transaction cap, rolling
    window, unknown-counterparty cap, human-approval threshold. A DENY at any
    level beats a REQUIRE_HUMAN below it.
    """
    evaluated = {
        "amount": amount,
        "currency": str(currency),
        "per_txn_cap": limits.per_txn_cap,
        "rolling_window_cap": limits.rolling_window_cap,
        "rolling_spend": ctx.rolling_spend,
        "human_approval_threshold": limits.human_approval_threshold,
        "unknown_counterparty_cap": limits.unknown_counterparty_cap,
        "counterparty_confidence": ctx.counterparty_confidence,
        "confidence_floor": limits.confidence_floor,
        "actor_status": str(ctx.actor_status),
    }

    def decide(verdict: Verdict, reason: str) -> PolicyDecision:
        return PolicyDecision(
            decision_id=new_id("dec"),
            action_ref=action_ref,
            actor_id=actor_id,
            verdict=verdict,
            reason=reason,
            limits_evaluated=evaluated,
            ts=datetime.now(timezone.utc).isoformat(),
        )

    if ctx.actor_status == ActorStatus.FROZEN:
        return decide(Verdict.DENY, "Actor is frozen pending reconciliation")

    if amount > limits.per_txn_cap:
        return decide(
            Verdict.DENY,
            f"Amount {amount} exceeds per-transaction cap {limits.per_txn_cap}",
        )

    if ctx.rolling_spend + amount > limits.rolling_window_cap:
        return decide(
            Verdict.DENY,
            f"Amount {amount} would breach rolling window cap "
            f"{limits.rolling_window_cap} (spent {ctx.rolling_spend})",
        )

    if (
        ctx.counterparty_confidence < limits.confidence_floor
        and amount > limits.unknown_counterparty_cap
    ):
        return decide(
            Verdict.DENY,
            f"Amount {amount} exceeds unknown counterparty cap "
            f"{limits.unknown_counterparty_cap} at confidence "
            f"{ctx.counterparty_confidence:.2f}",
        )

    if amount >= limits.human_approval_threshold:
        return decide(
            Verdict.REQUIRE_HUMAN,
            f"Amount {amount} is at or above the human approval threshold "
            f"{limits.human_approval_threshold}",
        )

    return decide(Verdict.ALLOW, "Within all configured limits")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_policy.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/exchange/policy.py tests/test_policy.py
git commit -m "feat: policy gate with explainable verdicts and trial-size bounding"
```

---

### Task 6: Hybrid retrieval

**Files:**
- Create: `src/exchange/retrieval.py`
- Test: `tests/test_retrieval.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `exchange.retrieval.rrf_fuse(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]`
  - `exchange.retrieval.HybridIndex(embed_fn: Callable[[list[str]], list[list[float]]] | None = None)` with `index(docs: list[tuple[str, str]]) -> None` and `search(query: str, top_k: int = 5) -> list[tuple[str, float]]`
  - `exchange.retrieval.default_embedder() -> Callable[[list[str]], list[list[float]]]`

`embed_fn` is injectable so tests never load a transformer model. `default_embedder()` lazily loads `sentence-transformers` only when called.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_retrieval.py`:

```python
import math

from exchange.retrieval import HybridIndex, rrf_fuse

DOCS = [
    ("ast_1", "corrugated kraft boxes 12x8 recyclable"),
    ("ast_2", "biodegradable mailers compostable poly"),
    ("ast_3", "bubble wrap rolls plastic protective"),
    ("ast_4", "vitamin c serum 20% skincare"),
]


def fake_embedder(texts):
    """Deterministic bag-of-words vectors over a fixed vocabulary.

    Keeps tests fast and offline while still exercising the dense path.
    """
    vocab = [
        "corrugated", "kraft", "boxes", "recyclable",
        "biodegradable", "mailers", "compostable", "poly",
        "bubble", "wrap", "plastic", "protective",
        "vitamin", "serum", "skincare", "eco",
    ]
    vectors = []
    for text in texts:
        tokens = set(text.lower().split())
        vec = [1.0 if word in tokens else 0.0 for word in vocab]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        vectors.append([v / norm for v in vec])
    return vectors


def test_rrf_fuse_ranks_a_doc_appearing_high_in_both_lists_first():
    fused = rrf_fuse([["a", "b", "c"], ["a", "c", "b"]])

    assert fused[0][0] == "a"


def test_rrf_fuse_includes_docs_present_in_only_one_ranking():
    fused = rrf_fuse([["a", "b"], ["c"]])
    ids = [doc_id for doc_id, _ in fused]

    assert set(ids) == {"a", "b", "c"}


def test_rrf_fuse_scores_descend():
    fused = rrf_fuse([["a", "b", "c"], ["a", "b", "c"]])
    scores = [score for _, score in fused]

    assert scores == sorted(scores, reverse=True)


def test_rrf_fuse_of_nothing_is_empty():
    assert rrf_fuse([]) == []


def test_exact_term_match_is_retrieved():
    index = HybridIndex(embed_fn=fake_embedder)
    index.index(DOCS)

    results = index.search("corrugated boxes", top_k=2)

    assert results[0][0] == "ast_1"


def test_paraphrase_is_retrieved_via_the_dense_path():
    """'eco ... packaging' contributes no BM25 signal; 'biodegradable' carries it.

    Both retrievers should agree on ast_2 here — the point is that the fused
    ranking surfaces it, not that either path finds it alone.
    """
    index = HybridIndex(embed_fn=fake_embedder)
    index.index(DOCS)

    results = index.search("eco biodegradable packaging", top_k=2)
    ids = [doc_id for doc_id, _ in results]

    assert "ast_2" in ids


def test_unrelated_document_ranks_below_relevant_ones():
    index = HybridIndex(embed_fn=fake_embedder)
    index.index(DOCS)

    results = index.search("corrugated kraft boxes", top_k=4)
    ids = [doc_id for doc_id, _ in results]

    assert ids.index("ast_1") < ids.index("ast_4")


def test_top_k_bounds_the_result_count():
    index = HybridIndex(embed_fn=fake_embedder)
    index.index(DOCS)

    assert len(index.search("packaging", top_k=2)) == 2


def test_searching_an_empty_index_returns_nothing():
    index = HybridIndex(embed_fn=fake_embedder)
    index.index([])

    assert index.search("anything") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_retrieval.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'exchange.retrieval'`

- [ ] **Step 3: Implement `retrieval.py`**

Create `src/exchange/retrieval.py`:

```python
"""Hybrid retrieval: BM25 for exact terms, embeddings for intent, fused by RRF.

Sparse retrieval catches SKUs, materials, and brand names. Dense retrieval
catches paraphrase — 'eco packaging' finding 'biodegradable mailers'. Neither
alone is sufficient for a descriptive bid, so both run and their rankings are
combined by reciprocal rank fusion.
"""
from __future__ import annotations

from typing import Callable

from rank_bm25 import BM25Okapi

Embedder = Callable[[list[str]], list[list[float]]]


def rrf_fuse(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal rank fusion.

    Each ranking contributes 1/(k + rank) to a document's score. Rank position
    matters; the underlying scores do not, which is what lets two retrievers
    with incomparable score scales be combined at all.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


def default_embedder() -> Embedder:
    """Load sentence-transformers lazily — importing it is slow."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("all-MiniLM-L6-v2")

    def embed(texts: list[str]) -> list[list[float]]:
        return model.encode(texts, normalize_embeddings=True).tolist()

    return embed


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class HybridIndex:
    def __init__(self, embed_fn: Embedder | None = None) -> None:
        self._embed = embed_fn or default_embedder()
        self._doc_ids: list[str] = []
        self._texts: list[str] = []
        self._bm25: BM25Okapi | None = None
        self._vectors: list[list[float]] = []

    def index(self, docs: list[tuple[str, str]]) -> None:
        self._doc_ids = [doc_id for doc_id, _ in docs]
        self._texts = [text for _, text in docs]
        if not docs:
            self._bm25 = None
            self._vectors = []
            return
        self._bm25 = BM25Okapi([t.lower().split() for t in self._texts])
        self._vectors = self._embed(self._texts)

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        if not self._doc_ids:
            return []

        sparse_scores = self._bm25.get_scores(query.lower().split())
        sparse_ranking = [
            self._doc_ids[i]
            for i in sorted(
                range(len(self._doc_ids)), key=lambda i: sparse_scores[i], reverse=True
            )
        ]

        query_vec = self._embed([query])[0]
        dense_scores = [_cosine(query_vec, v) for v in self._vectors]
        dense_ranking = [
            self._doc_ids[i]
            for i in sorted(
                range(len(self._doc_ids)), key=lambda i: dense_scores[i], reverse=True
            )
        ]

        return rrf_fuse([sparse_ranking, dense_ranking])[:top_k]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_retrieval.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/exchange/retrieval.py tests/test_retrieval.py
git commit -m "feat: hybrid BM25 + dense retrieval fused by RRF"
```

---

### Task 7: Matching engine

**Files:**
- Create: `src/exchange/matching.py`
- Test: `tests/test_matching.py`

**Interfaces:**
- Consumes: `exchange.models.{Order, Asset, Match, Side}`, `exchange.retrieval.HybridIndex`, `exchange.ids.new_id`.
- Produces: `exchange.matching.find_candidates(bid: Order, asks: list[Order], assets: dict[str, Asset], index: HybridIndex, counterparty_scores: dict[str, float] | None = None, top_k: int = 3) -> list[Match]`

Rules: feasibility first (price within `limit_price`, `qty` available, side is ASK, actor differs from bidder), then retrieval rank, then counterparty score as a **soft additive term capped at 20% of the retrieval score** — it can reorder but never exclude. Clearing price is the ask's `limit_price`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_matching.py`:

```python
from exchange.matching import find_candidates
from exchange.models import Asset, AssetKind, Currency, Order, Side
from exchange.retrieval import HybridIndex
from tests.test_retrieval import fake_embedder


def _ask(order_id, actor_id, asset_ref, unit_price, qty=1000):
    return Order(
        order_id=order_id,
        actor_id=actor_id,
        side=Side.ASK,
        asset_ref=asset_ref,
        asset_query=None,
        qty=qty,
        limit_price=unit_price,
        currency=Currency.INR,
        expires_at="2026-09-30T00:00:00+00:00",
        policy_snapshot={},
    )


def _asset(asset_id, title, actor_id):
    return Asset(
        asset_id=asset_id,
        kind=AssetKind.GOODS,
        title=title,
        spec={},
        currency=Currency.INR,
        origin_actor_id=actor_id,
    )


BID = Order(
    order_id="ord_bid",
    actor_id="m_buyer",
    side=Side.BID,
    asset_ref=None,
    asset_query={"text": "biodegradable mailers compostable"},
    qty=500,
    limit_price=2200,
    currency=Currency.INR,
    expires_at="2026-09-30T00:00:00+00:00",
    policy_snapshot={},
)

ASSETS = {
    "ast_1": _asset("ast_1", "corrugated kraft boxes recyclable", "m_a"),
    "ast_2": _asset("ast_2", "biodegradable mailers compostable poly", "m_b"),
    "ast_3": _asset("ast_3", "bubble wrap plastic protective", "m_c"),
}


def _index():
    index = HybridIndex(embed_fn=fake_embedder)
    index.index([(a.asset_id, a.title) for a in ASSETS.values()])
    return index


def test_returns_the_semantically_matching_ask_first():
    asks = [
        _ask("ord_1", "m_a", "ast_1", 1800),
        _ask("ord_2", "m_b", "ast_2", 1940),
        _ask("ord_3", "m_c", "ast_3", 1500),
    ]

    matches = find_candidates(BID, asks, ASSETS, _index())

    assert matches[0].ask_order_id == "ord_2"


def test_asks_above_the_bid_limit_are_excluded():
    asks = [_ask("ord_2", "m_b", "ast_2", 2500)]

    assert find_candidates(BID, asks, ASSETS, _index()) == []


def test_asks_with_insufficient_quantity_are_excluded():
    asks = [_ask("ord_2", "m_b", "ast_2", 1940, qty=100)]

    assert find_candidates(BID, asks, ASSETS, _index()) == []


def test_the_bidders_own_ask_is_excluded():
    asks = [_ask("ord_2", "m_buyer", "ast_2", 1940)]

    assert find_candidates(BID, asks, ASSETS, _index()) == []


def test_clearing_price_is_the_ask_price():
    asks = [_ask("ord_2", "m_b", "ast_2", 1940)]

    assert find_candidates(BID, asks, ASSETS, _index())[0].clearing_price == 1940


def test_match_carries_a_human_readable_rationale():
    asks = [_ask("ord_2", "m_b", "ast_2", 1940)]

    rationale = find_candidates(BID, asks, ASSETS, _index())[0].rationale

    assert "ast_2" in rationale
    assert "1940" in rationale


def test_counterparty_score_can_reorder_near_ties():
    """Two identical listings; the better-regarded seller comes first."""
    assets = {
        "ast_2": _asset("ast_2", "biodegradable mailers compostable poly", "m_b"),
        "ast_4": _asset("ast_4", "biodegradable mailers compostable poly", "m_d"),
    }
    index = HybridIndex(embed_fn=fake_embedder)
    index.index([(a.asset_id, a.title) for a in assets.values()])
    asks = [_ask("ord_2", "m_b", "ast_2", 1940), _ask("ord_4", "m_d", "ast_4", 1940)]

    matches = find_candidates(
        BID, asks, assets, index, counterparty_scores={"m_b": 0.1, "m_d": 0.95}
    )

    assert matches[0].ask_order_id == "ord_4"


def test_counterparty_score_never_excludes_an_ask():
    """A distrusted seller is still offered — the gate bounds them, not the match."""
    asks = [_ask("ord_2", "m_b", "ast_2", 1940)]

    matches = find_candidates(
        BID, asks, ASSETS, _index(), counterparty_scores={"m_b": 0.0}
    )

    assert len(matches) == 1


def test_top_k_bounds_the_candidate_count():
    asks = [
        _ask("ord_1", "m_a", "ast_1", 1800),
        _ask("ord_2", "m_b", "ast_2", 1940),
        _ask("ord_3", "m_c", "ast_3", 1500),
    ]

    assert len(find_candidates(BID, asks, ASSETS, _index(), top_k=2)) == 2


def test_no_asks_yields_no_matches():
    assert find_candidates(BID, [], ASSETS, _index()) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_matching.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'exchange.matching'`

- [ ] **Step 3: Implement `matching.py`**

Create `src/exchange/matching.py`:

```python
"""Match a bid against the open asks.

Feasibility is a hard filter. Retrieval decides relevance. Counterparty
standing is a soft nudge that can reorder near-ties but can never exclude an
ask — exclusion by reputation is what ossifies a market into cliques. Risk on
unfamiliar counterparties is bounded by the policy gate instead.
"""
from __future__ import annotations

from exchange.ids import new_id
from exchange.models import Asset, Match, Order, Side
from exchange.retrieval import HybridIndex

# The counterparty nudge is capped at this fraction of the retrieval score.
COUNTERPARTY_WEIGHT = 0.2


def find_candidates(
    bid: Order,
    asks: list[Order],
    assets: dict[str, Asset],
    index: HybridIndex,
    counterparty_scores: dict[str, float] | None = None,
    top_k: int = 3,
) -> list[Match]:
    counterparty_scores = counterparty_scores or {}

    feasible = {
        ask.asset_ref: ask
        for ask in asks
        if ask.side == Side.ASK
        and ask.actor_id != bid.actor_id
        and ask.asset_ref in assets
        and ask.limit_price <= bid.limit_price
        and ask.qty >= bid.qty
    }
    if not feasible:
        return []

    query = bid.asset_query.get("text", "") if bid.asset_query else ""
    if not query and bid.asset_ref:
        query = assets[bid.asset_ref].title

    ranked = index.search(query, top_k=len(assets))

    scored: list[tuple[float, Match]] = []
    for asset_id, retrieval_score in ranked:
        ask = feasible.get(asset_id)
        if ask is None:
            continue

        standing = counterparty_scores.get(ask.actor_id, 0.5)
        final_score = retrieval_score * (1.0 + COUNTERPARTY_WEIGHT * (standing - 0.5) * 2)

        scored.append((
            final_score,
            Match(
                match_id=new_id("mch"),
                bid_order_id=bid.order_id,
                ask_order_id=ask.order_id,
                clearing_price=ask.limit_price,
                score=final_score,
                rationale=(
                    f"{asset_id} matched '{query}' at {ask.limit_price} "
                    f"(<= bid limit {bid.limit_price}), qty {ask.qty} >= {bid.qty}, "
                    f"counterparty {ask.actor_id} standing {standing:.2f}"
                ),
            ),
        ))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [match for _, match in scored[:top_k]]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_matching.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/exchange/matching.py tests/test_matching.py
git commit -m "feat: matching engine with soft counterparty weighting"
```

---

### Task 8: Settlement rails

**Files:**
- Create: `src/exchange/rails/__init__.py`
- Create: `src/exchange/rails/base.py`
- Create: `src/exchange/rails/credits.py`
- Create: `src/exchange/rails/inr.py`
- Test: `tests/test_rails.py`

**Interfaces:**
- Consumes: `exchange.eventlog.EventLog`, `exchange.models.*`, `exchange.projections.fold`, `exchange.ids.new_id`.
- Produces:
  - `exchange.rails.base.SettlementRail` — protocol with `settle(match_id: str, from_actor_id: str, to_actor_id: str, amount: int, correlation_id: str, causation_id: str | None = None) -> Settlement`
  - `exchange.rails.credits.CreditRail(log: EventLog)`
  - `exchange.rails.inr.RazorpayRail(log: EventLog, client)` — `client` is any object exposing `order.create(dict) -> dict` and `order.payments(str) -> dict`, so tests inject a fake
  - `exchange.rails.base.InsufficientCredits` exception

**Note:** `RazorpayRail.settle` creates the order and records `SETTLEMENT_INITIATED`, then polls `order.payments()` for a captured payment. Whether a payment can appear without a browser is what Task 1 determined — if Task 1 found Payment Links are the route, add a `payment_link_url` field to the initiated event's payload and keep the polling loop as written.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rails.py`:

```python
import pytest

from exchange.eventlog import EventLog
from exchange.events import (
    CREDITS_TRANSFERRED,
    SETTLEMENT_COMPLETED,
    SETTLEMENT_FAILED,
    SETTLEMENT_INITIATED,
)
from exchange.models import SettlementStatus
from exchange.projections import fold
from exchange.rails.base import InsufficientCredits
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail


@pytest.fixture
def log(tmp_path):
    lg = EventLog(str(tmp_path / "rails.db"))
    yield lg
    lg.close()


class FakeRazorpay:
    """Stands in for razorpay.Client. `payments_by_order` drives the outcome."""

    def __init__(self, payments_by_order=None, fail_on_create=False):
        self._payments = payments_by_order or {}
        self._fail = fail_on_create
        self.created = []
        self.order = self._Orders(self)

    class _Orders:
        def __init__(self, outer):
            self._outer = outer

        def create(self, data):
            if self._outer._fail:
                raise RuntimeError("razorpay unreachable")
            order_id = f"order_{len(self._outer.created) + 1}"
            self._outer.created.append(data)
            return {"id": order_id, "amount": data["amount"], "status": "created"}

        def payments(self, order_id):
            return self._outer._payments.get(order_id, {"count": 0, "items": []})


# --- credits rail -----------------------------------------------------------

def test_credit_transfer_completes_and_moves_balances(log):
    log.append("house", CREDITS_TRANSFERRED,
               {"from_actor_id": "house", "to_actor_id": "m_a", "amount": 5000},
               correlation_id="seed")
    rail = CreditRail(log)

    settlement = rail.settle("mch_1", "m_a", "m_b", 1200, correlation_id="c1")

    assert settlement.status == SettlementStatus.COMPLETED
    state = fold(log.read_all())
    assert state.credit_balances["m_a"] == 3800
    assert state.credit_balances["m_b"] == 1200


def test_credit_transfer_is_refused_when_the_balance_is_short(log):
    log.append("house", CREDITS_TRANSFERRED,
               {"from_actor_id": "house", "to_actor_id": "m_a", "amount": 100},
               correlation_id="seed")
    rail = CreditRail(log)

    with pytest.raises(InsufficientCredits):
        rail.settle("mch_1", "m_a", "m_b", 1200, correlation_id="c1")


def test_refused_credit_transfer_writes_no_transfer_event(log):
    rail = CreditRail(log)

    with pytest.raises(InsufficientCredits):
        rail.settle("mch_1", "m_a", "m_b", 1200, correlation_id="c1")

    types = [e.type for e in log.read_by_correlation("c1")]
    assert CREDITS_TRANSFERRED not in types


def test_credit_settlement_events_carry_the_correlation_id(log):
    log.append("house", CREDITS_TRANSFERRED,
               {"from_actor_id": "house", "to_actor_id": "m_a", "amount": 5000},
               correlation_id="seed")
    rail = CreditRail(log)

    rail.settle("mch_1", "m_a", "m_b", 1200, correlation_id="c1")

    assert len(log.read_by_correlation("c1")) == 3  # initiated, transferred, completed


# --- INR rail ---------------------------------------------------------------

def test_razorpay_settlement_completes_when_a_payment_is_captured(log):
    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_abc", "status": "captured"}]}
    })
    rail = RazorpayRail(log, fake)

    settlement = rail.settle("mch_1", "m_buyer", "m_seller", 970000, correlation_id="c1")

    assert settlement.status == SettlementStatus.COMPLETED
    assert settlement.razorpay_order_id == "order_1"
    assert settlement.razorpay_payment_id == "pay_abc"


def test_razorpay_settlement_sends_the_amount_in_paise(log):
    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_abc", "status": "captured"}]}
    })
    rail = RazorpayRail(log, fake)

    rail.settle("mch_1", "m_buyer", "m_seller", 970000, correlation_id="c1")

    assert fake.created[0]["amount"] == 970000
    assert fake.created[0]["currency"] == "INR"


def test_razorpay_settlement_stays_pending_when_no_payment_arrives(log):
    fake = FakeRazorpay(payments_by_order={})
    rail = RazorpayRail(log, fake, poll_attempts=2, poll_interval=0)

    settlement = rail.settle("mch_1", "m_buyer", "m_seller", 970000, correlation_id="c1")

    assert settlement.status == SettlementStatus.PENDING
    types = [e.type for e in log.read_by_correlation("c1")]
    assert SETTLEMENT_INITIATED in types
    assert SETTLEMENT_COMPLETED not in types


def test_razorpay_failure_to_create_an_order_is_logged_as_failed(log):
    rail = RazorpayRail(log, FakeRazorpay(fail_on_create=True))

    settlement = rail.settle("mch_1", "m_buyer", "m_seller", 970000, correlation_id="c1")

    assert settlement.status == SettlementStatus.FAILED
    assert SETTLEMENT_FAILED in [e.type for e in log.read_by_correlation("c1")]


def test_an_uncaptured_payment_does_not_complete_the_settlement(log):
    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_abc", "status": "authorized"}]}
    })
    rail = RazorpayRail(log, fake, poll_attempts=1, poll_interval=0)

    settlement = rail.settle("mch_1", "m_buyer", "m_seller", 970000, correlation_id="c1")

    assert settlement.status == SettlementStatus.PENDING
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_rails.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'exchange.rails'`

- [ ] **Step 3: Implement `rails/base.py`**

Create `src/exchange/rails/__init__.py` as an empty file, then `src/exchange/rails/base.py`:

```python
"""The settlement rail interface, shared by both currencies."""
from __future__ import annotations

from typing import Protocol

from exchange.models import Settlement


class InsufficientCredits(Exception):
    """Raised when an actor's point balance cannot cover a transfer."""


class SettlementRail(Protocol):
    def settle(
        self,
        match_id: str,
        from_actor_id: str,
        to_actor_id: str,
        amount: int,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> Settlement:
        ...
```

- [ ] **Step 4: Implement `rails/credits.py`**

Create `src/exchange/rails/credits.py`:

```python
"""The CREDITS rail — an atomic ledger transfer.

Points never leave the system, so settlement is a single balance check
followed by a transfer event. Conservation is the invariant that matters.
"""
from __future__ import annotations

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.ids import new_id
from exchange.models import Currency, Settlement, SettlementStatus
from exchange.projections import fold
from exchange.rails.base import InsufficientCredits


class CreditRail:
    def __init__(self, log: EventLog) -> None:
        self._log = log

    def settle(
        self,
        match_id: str,
        from_actor_id: str,
        to_actor_id: str,
        amount: int,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> Settlement:
        balance = fold(self._log.read_all()).credit_balances.get(from_actor_id, 0)
        if balance < amount:
            raise InsufficientCredits(
                f"{from_actor_id} holds {balance} points, needs {amount}"
            )

        settlement_id = new_id("stl")

        initiated = self._log.append(
            from_actor_id,
            ev.SETTLEMENT_INITIATED,
            {
                "settlement_id": settlement_id,
                "match_id": match_id,
                "currency": str(Currency.CREDITS),
                "amount": amount,
            },
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

        transferred = self._log.append(
            from_actor_id,
            ev.CREDITS_TRANSFERRED,
            {
                "from_actor_id": from_actor_id,
                "to_actor_id": to_actor_id,
                "amount": amount,
                "settlement_id": settlement_id,
            },
            correlation_id=correlation_id,
            causation_id=initiated.event_id,
        )

        self._log.append(
            from_actor_id,
            ev.SETTLEMENT_COMPLETED,
            {"settlement_id": settlement_id},
            correlation_id=correlation_id,
            causation_id=transferred.event_id,
        )

        return Settlement(
            settlement_id=settlement_id,
            match_id=match_id,
            currency=Currency.CREDITS,
            amount=amount,
            status=SettlementStatus.COMPLETED,
        )
```

- [ ] **Step 5: Implement `rails/inr.py`**

Create `src/exchange/rails/inr.py`:

```python
"""The INR rail — Razorpay test mode.

Creates an order, then waits for a captured payment against it. A settlement
that never sees a capture stays PENDING rather than failing: pending is a real
state the accountant later reconciles, and pretending otherwise is exactly the
drift this system is built to catch.
"""
from __future__ import annotations

import time

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.ids import new_id
from exchange.models import Currency, Settlement, SettlementStatus


class RazorpayRail:
    def __init__(
        self,
        log: EventLog,
        client,
        poll_attempts: int = 3,
        poll_interval: float = 1.0,
    ) -> None:
        self._log = log
        self._client = client
        self._poll_attempts = poll_attempts
        self._poll_interval = poll_interval

    def settle(
        self,
        match_id: str,
        from_actor_id: str,
        to_actor_id: str,
        amount: int,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> Settlement:
        settlement_id = new_id("stl")

        try:
            order = self._client.order.create({
                "amount": amount,
                "currency": "INR",
                "receipt": settlement_id,
                "notes": {
                    "match_id": match_id,
                    "buyer": from_actor_id,
                    "seller": to_actor_id,
                },
            })
        except Exception as exc:  # noqa: BLE001 - any SDK failure is a failed settlement
            self._log.append(
                from_actor_id,
                ev.SETTLEMENT_FAILED,
                {
                    "settlement_id": settlement_id,
                    "match_id": match_id,
                    "reason": f"{type(exc).__name__}: {exc}",
                },
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            return Settlement(
                settlement_id=settlement_id,
                match_id=match_id,
                currency=Currency.INR,
                amount=amount,
                status=SettlementStatus.FAILED,
            )

        initiated = self._log.append(
            from_actor_id,
            ev.SETTLEMENT_INITIATED,
            {
                "settlement_id": settlement_id,
                "match_id": match_id,
                "currency": str(Currency.INR),
                "amount": amount,
                "razorpay_order_id": order["id"],
            },
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

        payment_id = self._await_capture(order["id"])
        if payment_id is None:
            return Settlement(
                settlement_id=settlement_id,
                match_id=match_id,
                currency=Currency.INR,
                amount=amount,
                status=SettlementStatus.PENDING,
                razorpay_order_id=order["id"],
            )

        self._log.append(
            from_actor_id,
            ev.SETTLEMENT_COMPLETED,
            {"settlement_id": settlement_id, "razorpay_payment_id": payment_id},
            correlation_id=correlation_id,
            causation_id=initiated.event_id,
        )

        return Settlement(
            settlement_id=settlement_id,
            match_id=match_id,
            currency=Currency.INR,
            amount=amount,
            status=SettlementStatus.COMPLETED,
            razorpay_order_id=order["id"],
            razorpay_payment_id=payment_id,
        )

    def _await_capture(self, razorpay_order_id: str) -> str | None:
        for attempt in range(self._poll_attempts):
            payments = self._client.order.payments(razorpay_order_id)
            for item in payments.get("items", []):
                if item.get("status") == "captured":
                    return item["id"]
            if attempt < self._poll_attempts - 1:
                time.sleep(self._poll_interval)
        return None
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_rails.py -v`
Expected: 9 passed

- [ ] **Step 7: Commit**

```bash
git add src/exchange/rails/ tests/test_rails.py
git commit -m "feat: INR and CREDITS settlement rails behind a shared interface"
```

---

### Task 9: The exchange service — gate, settle, log as one flow

**Files:**
- Create: `src/exchange/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: everything from Tasks 2–8.
- Produces: `exchange.service.Exchange(log: EventLog, index: HybridIndex, inr_rail, credit_rail)` with:
  - `register_actor(actor: Actor) -> None`
  - `list_asset(asset: Asset) -> None`
  - `post_order(order: Order, correlation_id: str) -> None`
  - `execute_match(match: Match, buyer_id: str, seller_id: str, ctx: PolicyContext, correlation_id: str, currency: Currency = Currency.INR) -> tuple[PolicyDecision, Settlement | None]`
  - `state() -> ExchangeState`
  - attributes `log: EventLog` and `index: HybridIndex` are public — tests and later plans read them directly

`execute_match` always logs a `POLICY_DECIDED` event, and only settles on `ALLOW`. This is the single chokepoint through which money moves.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_service.py`:

```python
import pytest

from exchange.eventlog import EventLog
from exchange.events import POLICY_DECIDED, SETTLEMENT_COMPLETED, SETTLEMENT_INITIATED
from exchange.models import (
    Actor,
    ActorKind,
    ActorStatus,
    Asset,
    AssetKind,
    Currency,
    Match,
    Order,
    Side,
    Verdict,
)
from exchange.policy import DEFAULT_INR_LIMITS, PolicyContext
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex
from exchange.service import Exchange
from tests.test_rails import FakeRazorpay
from tests.test_retrieval import fake_embedder

TRUSTED = PolicyContext(
    actor_status=ActorStatus.ACTIVE, rolling_spend=0, counterparty_confidence=0.9
)


@pytest.fixture
def exchange(tmp_path):
    log = EventLog(str(tmp_path / "svc.db"))
    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_abc", "status": "captured"}]}
    })
    index = HybridIndex(embed_fn=fake_embedder)
    ex = Exchange(log, index, RazorpayRail(log, fake), CreditRail(log))
    yield ex
    log.close()


MATCH = Match(
    match_id="mch_1",
    bid_order_id="ord_bid",
    ask_order_id="ord_ask",
    clearing_price=1940,
    score=0.9,
    rationale="test",
)


def test_registering_an_actor_puts_it_in_state(exchange):
    exchange.register_actor(Actor(actor_id="m_a", kind=ActorKind.MERCHANT))

    assert exchange.state().actors["m_a"].kind == ActorKind.MERCHANT


def test_listing_an_asset_puts_it_in_state_and_the_index(exchange):
    exchange.list_asset(Asset(
        asset_id="ast_1",
        kind=AssetKind.GOODS,
        title="biodegradable mailers",
        spec={},
        currency=Currency.INR,
        origin_actor_id="m_b",
    ))

    assert exchange.state().assets["ast_1"].title == "biodegradable mailers"
    assert exchange.index.search("mailers")[0][0] == "ast_1"


def test_posting_an_order_puts_it_in_the_book(exchange):
    order = Order(
        order_id="ord_1",
        actor_id="m_a",
        side=Side.BID,
        asset_ref=None,
        asset_query={"text": "mailers"},
        qty=500,
        limit_price=2200,
        currency=Currency.INR,
        expires_at="2026-09-30T00:00:00+00:00",
        policy_snapshot={},
    )

    exchange.post_order(order, correlation_id="c1")

    assert "ord_1" in exchange.state().open_orders


def test_allowed_match_settles_and_logs_the_decision_first(exchange):
    decision, settlement = exchange.execute_match(
        MATCH, "m_buyer", "m_seller", TRUSTED, correlation_id="c1"
    )

    assert decision.verdict == Verdict.ALLOW
    assert settlement is not None
    types = [e.type for e in exchange.log.read_by_correlation("c1")]
    assert types.index(POLICY_DECIDED) < types.index(SETTLEMENT_INITIATED)


def test_a_policy_decision_is_logged_even_when_the_verdict_is_allow(exchange):
    exchange.execute_match(MATCH, "m_buyer", "m_seller", TRUSTED, correlation_id="c1")

    decided = [e for e in exchange.log.read_by_correlation("c1") if e.type == POLICY_DECIDED]

    assert len(decided) == 1
    assert decided[0].payload["verdict"] == "ALLOW"


def test_denied_match_logs_the_decision_and_moves_no_money(exchange):
    frozen = PolicyContext(
        actor_status=ActorStatus.FROZEN, rolling_spend=0, counterparty_confidence=0.9
    )

    decision, settlement = exchange.execute_match(
        MATCH, "m_buyer", "m_seller", frozen, correlation_id="c1"
    )

    assert decision.verdict == Verdict.DENY
    assert settlement is None
    types = [e.type for e in exchange.log.read_by_correlation("c1")]
    assert SETTLEMENT_INITIATED not in types


def test_match_requiring_human_approval_does_not_settle(exchange):
    big = Match(
        match_id="mch_2",
        bid_order_id="ord_bid",
        ask_order_id="ord_ask",
        clearing_price=DEFAULT_INR_LIMITS.human_approval_threshold + 1,
        score=0.9,
        rationale="test",
    )

    decision, settlement = exchange.execute_match(
        big, "m_buyer", "m_seller", TRUSTED, correlation_id="c1"
    )

    assert decision.verdict == Verdict.REQUIRE_HUMAN
    assert settlement is None


def test_the_whole_story_is_recoverable_from_one_correlation_id(exchange):
    exchange.execute_match(MATCH, "m_buyer", "m_seller", TRUSTED, correlation_id="c1")

    types = [e.type for e in exchange.log.read_by_correlation("c1")]

    assert types == [POLICY_DECIDED, SETTLEMENT_INITIATED, SETTLEMENT_COMPLETED]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'exchange.service'`

- [ ] **Step 3: Implement `service.py`**

Create `src/exchange/service.py`:

```python
"""The exchange service — the single chokepoint through which money moves.

Nothing settles without passing `execute_match`, and `execute_match` always
records its policy decision before acting. That ordering is the audit trail's
guarantee: the gate is visible even when it says yes.
"""
from __future__ import annotations

from dataclasses import asdict

from exchange import events as ev
from exchange import policy
from exchange.eventlog import EventLog
from exchange.models import (
    Actor,
    Asset,
    Currency,
    Match,
    Order,
    PolicyDecision,
    Settlement,
    Verdict,
)
from exchange.policy import PolicyContext
from exchange.projections import ExchangeState, fold
from exchange.retrieval import HybridIndex


class Exchange:
    def __init__(
        self,
        log: EventLog,
        index: HybridIndex,
        inr_rail,
        credit_rail,
    ) -> None:
        self.log = log
        self.index = index
        self._inr_rail = inr_rail
        self._credit_rail = credit_rail
        self._indexed: list[tuple[str, str]] = []

    def register_actor(self, actor: Actor) -> None:
        self.log.append(
            actor.actor_id,
            ev.ACTOR_REGISTERED,
            _serialize(actor),
            correlation_id=f"reg_{actor.actor_id}",
        )

    def list_asset(self, asset: Asset) -> None:
        self.log.append(
            asset.origin_actor_id,
            ev.ASSET_LISTED,
            _serialize(asset),
            correlation_id=f"lst_{asset.asset_id}",
        )
        self._indexed.append((asset.asset_id, f"{asset.title} {_spec_text(asset)}"))
        self.index.index(self._indexed)

    def post_order(self, order: Order, correlation_id: str) -> None:
        self.log.append(
            order.actor_id,
            ev.ORDER_POSTED,
            _serialize(order),
            correlation_id=correlation_id,
        )

    def execute_match(
        self,
        match: Match,
        buyer_id: str,
        seller_id: str,
        ctx: PolicyContext,
        correlation_id: str,
        currency: Currency = Currency.INR,
    ) -> tuple[PolicyDecision, Settlement | None]:
        limits = (
            policy.DEFAULT_INR_LIMITS
            if currency == Currency.INR
            else policy.DEFAULT_CREDIT_LIMITS
        )

        decision = policy.evaluate(
            action_ref=match.match_id,
            actor_id=buyer_id,
            amount=match.clearing_price,
            currency=currency,
            ctx=ctx,
            limits=limits,
        )

        decision_event = self.log.append(
            buyer_id,
            ev.POLICY_DECIDED,
            _serialize(decision),
            correlation_id=correlation_id,
        )

        if decision.verdict != Verdict.ALLOW:
            return decision, None

        rail = self._inr_rail if currency == Currency.INR else self._credit_rail
        settlement = rail.settle(
            match_id=match.match_id,
            from_actor_id=buyer_id,
            to_actor_id=seller_id,
            amount=match.clearing_price,
            correlation_id=correlation_id,
            causation_id=decision_event.event_id,
        )
        return decision, settlement

    def state(self) -> ExchangeState:
        return fold(self.log.read_all())


def _serialize(record) -> dict:
    """Dataclass to JSON-safe dict. StrEnum members serialize as their value."""
    return {k: (str(v) if hasattr(v, "value") else v) for k, v in asdict(record).items()}


def _spec_text(asset: Asset) -> str:
    return " ".join(str(v) for v in asset.spec.values())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/exchange/service.py tests/test_service.py
git commit -m "feat: exchange service gating every settlement behind a logged decision"
```

---

### Task 10: End-to-end walkthrough and invariant properties

The acceptance test for Plan 1: a descriptive bid becomes a settled payment, and the whole story reads back from one `correlation_id`. Plus the invariants that must hold no matter what sequence of events occurs.

**Files:**
- Create: `tests/test_end_to_end.py`
- Create: `scripts/demo_trade.py`

**Interfaces:**
- Consumes: everything.
- Produces: nothing new — this task proves the system works.

- [ ] **Step 1: Write the failing end-to-end test**

Create `tests/test_end_to_end.py`:

```python
import pytest

from exchange.eventlog import EventLog
from exchange.events import (
    ASSET_LISTED,
    ORDER_POSTED,
    POLICY_DECIDED,
    SETTLEMENT_COMPLETED,
    SETTLEMENT_INITIATED,
)
from exchange.matching import find_candidates
from exchange.models import (
    Actor,
    ActorKind,
    ActorStatus,
    Asset,
    AssetKind,
    Currency,
    Order,
    SettlementStatus,
    Side,
    Verdict,
)
from exchange.policy import PolicyContext
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex
from exchange.service import Exchange
from tests.test_rails import FakeRazorpay
from tests.test_retrieval import fake_embedder

CORR = "corr_vitaminc_launch"


@pytest.fixture
def exchange(tmp_path):
    log = EventLog(str(tmp_path / "e2e.db"))
    fake = FakeRazorpay(payments_by_order={
        "order_1": {"count": 1, "items": [{"id": "pay_e2e", "status": "captured"}]}
    })
    ex = Exchange(
        log,
        HybridIndex(embed_fn=fake_embedder),
        RazorpayRail(log, fake),
        CreditRail(log),
    )
    yield ex
    log.close()


def _seed_market(exchange):
    for actor_id in ("m_buyer", "m_known", "m_unknown"):
        exchange.register_actor(Actor(actor_id=actor_id, kind=ActorKind.MERCHANT))

    exchange.list_asset(Asset(
        asset_id="ast_boxes", kind=AssetKind.GOODS,
        title="corrugated kraft boxes recyclable", spec={},
        currency=Currency.INR, origin_actor_id="m_known",
    ))
    exchange.list_asset(Asset(
        asset_id="ast_mailers", kind=AssetKind.GOODS,
        title="biodegradable mailers compostable poly", spec={},
        currency=Currency.INR, origin_actor_id="m_unknown",
    ))

    asks = [
        Order(
            order_id="ord_ask_boxes", actor_id="m_known", side=Side.ASK,
            asset_ref="ast_boxes", asset_query=None, qty=1000, limit_price=1800,
            currency=Currency.INR, expires_at="2026-09-30T00:00:00+00:00",
            policy_snapshot={},
        ),
        Order(
            order_id="ord_ask_mailers", actor_id="m_unknown", side=Side.ASK,
            asset_ref="ast_mailers", asset_query=None, qty=1000, limit_price=1940,
            currency=Currency.INR, expires_at="2026-09-30T00:00:00+00:00",
            policy_snapshot={},
        ),
    ]
    for ask in asks:
        exchange.post_order(ask, correlation_id=CORR)
    return asks


def test_descriptive_bid_settles_end_to_end(exchange):
    asks = _seed_market(exchange)

    bid = Order(
        order_id="ord_bid", actor_id="m_buyer", side=Side.BID,
        asset_ref=None,
        asset_query={"text": "biodegradable compostable mailers", "by": "2026-08-29"},
        qty=500, limit_price=2200, currency=Currency.INR,
        expires_at="2026-08-29T00:00:00+00:00", policy_snapshot={},
    )
    exchange.post_order(bid, correlation_id=CORR)

    matches = find_candidates(bid, asks, exchange.state().assets, exchange.index)
    assert matches[0].ask_order_id == "ord_ask_mailers"

    decision, settlement = exchange.execute_match(
        matches[0], "m_buyer", "m_unknown",
        PolicyContext(ActorStatus.ACTIVE, rolling_spend=0, counterparty_confidence=0.9),
        correlation_id=CORR,
    )

    assert decision.verdict == Verdict.ALLOW
    assert settlement.status == SettlementStatus.COMPLETED
    assert settlement.razorpay_payment_id == "pay_e2e"


def test_the_full_story_reads_back_from_one_correlation_id(exchange):
    asks = _seed_market(exchange)
    bid = Order(
        order_id="ord_bid", actor_id="m_buyer", side=Side.BID, asset_ref=None,
        asset_query={"text": "biodegradable compostable mailers"}, qty=500,
        limit_price=2200, currency=Currency.INR,
        expires_at="2026-08-29T00:00:00+00:00", policy_snapshot={},
    )
    exchange.post_order(bid, correlation_id=CORR)
    matches = find_candidates(bid, asks, exchange.state().assets, exchange.index)
    exchange.execute_match(
        matches[0], "m_buyer", "m_unknown",
        PolicyContext(ActorStatus.ACTIVE, 0, 0.9), correlation_id=CORR,
    )

    types = [e.type for e in exchange.log.read_by_correlation(CORR)]

    assert types == [
        ORDER_POSTED, ORDER_POSTED, ORDER_POSTED,
        POLICY_DECIDED, SETTLEMENT_INITIATED, SETTLEMENT_COMPLETED,
    ]


def test_an_unknown_counterparty_is_bounded_not_excluded(exchange):
    """The trial-size rule: the trade happens, but only a small one."""
    asks = _seed_market(exchange)
    bid = Order(
        order_id="ord_bid", actor_id="m_buyer", side=Side.BID, asset_ref=None,
        asset_query={"text": "biodegradable compostable mailers"}, qty=500,
        limit_price=2200, currency=Currency.INR,
        expires_at="2026-08-29T00:00:00+00:00", policy_snapshot={},
    )
    matches = find_candidates(
        bid, asks, exchange.state().assets, exchange.index,
        counterparty_scores={"m_unknown": 0.0},
    )
    assert matches, "an unknown counterparty must still be offered"

    unknown_ctx = PolicyContext(ActorStatus.ACTIVE, 0, counterparty_confidence=0.05)
    decision, _ = exchange.execute_match(
        matches[0], "m_buyer", "m_unknown", unknown_ctx, correlation_id=CORR
    )

    assert decision.verdict == Verdict.ALLOW  # 1940 is under the 500_000 trial cap


def test_every_settlement_is_preceded_by_an_allow_decision(exchange):
    """The invariant the accountant will later assert globally."""
    asks = _seed_market(exchange)
    bid = Order(
        order_id="ord_bid", actor_id="m_buyer", side=Side.BID, asset_ref=None,
        asset_query={"text": "biodegradable compostable mailers"}, qty=500,
        limit_price=2200, currency=Currency.INR,
        expires_at="2026-08-29T00:00:00+00:00", policy_snapshot={},
    )
    matches = find_candidates(bid, asks, exchange.state().assets, exchange.index)
    exchange.execute_match(
        matches[0], "m_buyer", "m_unknown",
        PolicyContext(ActorStatus.ACTIVE, 0, 0.9), correlation_id=CORR,
    )

    events = exchange.log.read_all()
    for i, event in enumerate(events):
        if event.type == SETTLEMENT_INITIATED:
            preceding = [e for e in events[:i] if e.type == POLICY_DECIDED]
            assert preceding, "settlement with no preceding decision"
            assert preceding[-1].payload["verdict"] == "ALLOW"


def test_state_folded_from_the_log_matches_live_state(exchange):
    _seed_market(exchange)

    from exchange.projections import fold

    assert fold(exchange.log.read_all()) == exchange.state()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_end_to_end.py -v`
Expected: FAIL — the fixtures import fine but assertions fail until Tasks 2–9 are complete. If Tasks 2–9 are done, these should already pass; if any fail, fix the underlying module rather than the test.

- [ ] **Step 3: Run the whole suite**

Run: `.venv/bin/pytest -v`
Expected: all tests pass across all eight test files.

- [ ] **Step 4: Write the live demo script**

Create `scripts/demo_trade.py`. This one hits real Razorpay test mode, unlike the tests:

```python
"""Run one real trade against Razorpay test mode and print the audit trail.

This is the Plan 1 acceptance check. It produces real order and payment ids.
"""
from __future__ import annotations

import sys

import razorpay

from exchange.config import Config
from exchange.eventlog import EventLog
from exchange.matching import find_candidates
from exchange.models import (
    Actor, ActorKind, ActorStatus, Asset, AssetKind, Currency, Order, Side,
)
from exchange.policy import PolicyContext
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex, default_embedder
from exchange.service import Exchange

CORR = "corr_demo_trade"


def main() -> int:
    cfg = Config.from_env()
    client = razorpay.Client(auth=(cfg.razorpay_key_id, cfg.razorpay_key_secret))

    log = EventLog(cfg.db_path)
    exchange = Exchange(
        log,
        HybridIndex(embed_fn=default_embedder()),
        RazorpayRail(log, client, poll_attempts=3, poll_interval=2.0),
        CreditRail(log),
    )

    for actor_id in ("m_buyer", "m_seller"):
        exchange.register_actor(Actor(actor_id=actor_id, kind=ActorKind.MERCHANT))

    exchange.list_asset(Asset(
        asset_id="ast_mailers", kind=AssetKind.GOODS,
        title="biodegradable mailers compostable poly 10x13",
        spec={"material": "compostable poly", "size": "10x13"},
        currency=Currency.INR, origin_actor_id="m_seller",
    ))

    ask = Order(
        order_id="ord_ask", actor_id="m_seller", side=Side.ASK,
        asset_ref="ast_mailers", asset_query=None, qty=1000, limit_price=1940,
        currency=Currency.INR, expires_at="2026-09-30T00:00:00+00:00",
        policy_snapshot={},
    )
    exchange.post_order(ask, correlation_id=CORR)

    bid = Order(
        order_id="ord_bid", actor_id="m_buyer", side=Side.BID, asset_ref=None,
        asset_query={"text": "eco friendly biodegradable mailers under 22 a unit"},
        qty=500, limit_price=2200, currency=Currency.INR,
        expires_at="2026-08-29T00:00:00+00:00", policy_snapshot={},
    )
    exchange.post_order(bid, correlation_id=CORR)

    matches = find_candidates(bid, [ask], exchange.state().assets, exchange.index)
    if not matches:
        print("No match found.")
        return 1
    print(f"Matched: {matches[0].rationale}\n")

    decision, settlement = exchange.execute_match(
        matches[0], "m_buyer", "m_seller",
        PolicyContext(ActorStatus.ACTIVE, rolling_spend=0, counterparty_confidence=0.9),
        correlation_id=CORR,
    )
    print(f"Policy: {decision.verdict} — {decision.reason}\n")

    if settlement:
        print(f"Settlement: {settlement.status}")
        print(f"  razorpay_order_id:   {settlement.razorpay_order_id}")
        print(f"  razorpay_payment_id: {settlement.razorpay_payment_id}\n")

    print("=== AUDIT TRAIL ===")
    for event in log.read_by_correlation(CORR):
        print(f"  [{event.seq:>3}] {event.ts}  {event.actor_id:<10} {event.type}")

    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the demo against real test mode**

Run: `.venv/bin/python scripts/demo_trade.py`

Expected: a match, an `ALLOW` decision, a real `order_...` id, and the audit trail printed in order. Whether `razorpay_payment_id` is populated depends on Task 1's findings — if payment requires a browser step, the settlement prints `PENDING`, which is correct and is exactly the state the accountant reconciles in Plan 3. Note the outcome in `docs/razorpay-test-mode-findings.md`.

- [ ] **Step 6: Commit**

```bash
git add tests/test_end_to_end.py scripts/demo_trade.py docs/razorpay-test-mode-findings.md
git commit -m "test: end-to-end descriptive bid to settled payment with audit trail"
```

---

## Plan 1 done when

- `.venv/bin/pytest` is green across all eight test files.
- `scripts/demo_trade.py` produces a real Razorpay test-mode order id.
- `docs/razorpay-test-mode-findings.md` records the real capture path, not a guess.
- The event log format is frozen — Plan 5's replay UI will be built against it, so any change after this point costs UI rework.

## What Plan 2 builds on

- `Exchange.execute_match` is the only way money moves. Brokers call it; they never touch a rail.
- `find_candidates(..., counterparty_scores=...)` is where the Diplomat's advice enters — as a dict of `actor_id -> float`, soft-weighted and unable to exclude.
- `PolicyContext.counterparty_confidence` is where the trial-size bound reads from. The Subconscious will supply it.
- Every broker action appends to the same log with the same `correlation_id` threading, so a broker's reasoning is replayable alongside its trades.
