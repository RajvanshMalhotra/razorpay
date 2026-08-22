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
