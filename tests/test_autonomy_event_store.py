"""Durability, replay and concurrency tests using temporary SQLite databases."""
import sqlite3

import pytest

from tools.autonomy.controller import Controller, State
from tools.autonomy.event_store import EventStore, IntegrityError
from tools.autonomy.mission_control import render_html


def test_persists_and_replays_across_instances(tmp_path):
    path = tmp_path / "events.db"
    store = EventStore(path)
    store.record_advance("R-1", evidence="discovery:1", passed=True)
    store.record_advance("R-1", evidence="triage:2", passed=True)
    restored = EventStore(path).replay("R-1")
    assert restored["state"] == State.PLAN.value
    assert len(restored["events"]) == 2
    assert "triage:2" in render_html(restored)


def test_rejects_stale_append(tmp_path):
    store = EventStore(tmp_path / "events.db")
    event = Controller("R-2")
    event.advance(evidence="e1", passed=True)
    store.append("R-2", event.events[-1], expected_sequence=0)
    with pytest.raises(IntegrityError, match="stale"):
        store.append("R-2", event.events[-1], expected_sequence=0)


def test_detects_modified_payload(tmp_path):
    path = tmp_path / "events.db"
    store = EventStore(path)
    store.record_advance("R-3", evidence="real", passed=True)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE autonomy_events SET payload=? WHERE task_id=?", ('{"tampered":true}', "R-3"))
    with pytest.raises(IntegrityError):
        store.replay("R-3")


def test_failed_gate_blocks_and_stays_terminal(tmp_path):
    store = EventStore(tmp_path / "events.db")
    assert store.record_advance("R-4", evidence="failed:ci", passed=False)["state"] == "BLOCKED"
    with pytest.raises(ValueError, match="Terminal"):
        store.record_advance("R-4", evidence="retry", passed=True)


def test_human_gate_stays_closed_without_approval(tmp_path):
    store = EventStore(tmp_path / "events.db")
    for i in range(6):
        store.record_advance("R-5", evidence=f"step:{i}", passed=True)
    assert store.replay("R-5")["state"] == "INTEGRATE"
    assert store.record_advance("R-5", evidence="ci:pass", passed=True)["state"] == "BLOCKED"


def test_isolation_between_tasks(tmp_path):
    store = EventStore(tmp_path / "events.db")
    store.record_advance("A", evidence="a", passed=True)
    assert store.replay("B") == {"task_id": "B", "state": "DISCOVER", "events": []}
