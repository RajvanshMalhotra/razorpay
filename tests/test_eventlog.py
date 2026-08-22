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
