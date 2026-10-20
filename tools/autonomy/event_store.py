"""SQLite append-only engineering event log with replay integrity checks.

Local prototype only. Never expose to agents as an approval authority.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

from .controller import Controller, State, NEXT, AGENTS


class IntegrityError(ValueError):
    """Recorded history is malformed or has been modified."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(previous, task_id, sequence, payload):
    body = _canonical({"previous": previous, "task_id": task_id, "sequence": sequence, "event": payload})
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class EventStore:
    def __init__(self, path):
        self.path = str(Path(path))
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS autonomy_events (
                task_id TEXT NOT NULL,
                sequence INTEGER NOT NULL CHECK (sequence > 0),
                payload TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                PRIMARY KEY (task_id, sequence)
            )""")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA busy_timeout = 10000")
        return db

    def append(self, task_id, event, *, expected_sequence):
        """Optimistic concurrency: append only after checking latest sequence.

        Caller must provide an event obtained from a validated controller transition.
        Event data is untrusted until replay validation.
        """
        if not isinstance(task_id, str) or not task_id or len(task_id) > 128:
            raise ValueError("Invalid task id")
        if not isinstance(event, dict) or event.get("task_id") != task_id:
            raise ValueError("Invalid event")
        if not isinstance(expected_sequence, int) or expected_sequence < 0:
            raise ValueError("Invalid sequence")
        payload = _canonical(event)
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT sequence, event_hash FROM autonomy_events WHERE task_id=? ORDER BY sequence DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
                last_sequence, previous = row if row else (0, "GENESIS")
                if last_sequence != expected_sequence:
                    raise IntegrityError("Concurrent append / stale sequence")
                sequence = last_sequence + 1
                event_hash = _digest(previous, task_id, sequence, event)
                db.execute(
                    "INSERT INTO autonomy_events (task_id,sequence,payload,previous_hash,event_hash) VALUES (?,?,?,?,?)",
                    (task_id, sequence, payload, previous, event_hash),
                )
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return sequence

    def replay(self, task_id):
        """Verify hash chain and legal transitions, reconstruct a read-only snapshot."""
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("Invalid task id")
        with self._connect() as db:
            rows = db.execute(
                "SELECT sequence,payload,previous_hash,event_hash FROM autonomy_events WHERE task_id=? ORDER BY sequence",
                (task_id,),
            ).fetchall()
        state = State.DISCOVER
        previous = "GENESIS"
        events = []
        for index, (sequence, raw, previous_hash, event_hash) in enumerate(rows, 1):
            if sequence != index or previous_hash != previous:
                raise IntegrityError("Sequence or chain mismatch")
            try:
                event = json.loads(raw)
            except (ValueError, TypeError) as exc:
                raise IntegrityError("Malformed event JSON") from exc
            if event_hash != _digest(previous, task_id, sequence, event):
                raise IntegrityError("Event hash mismatch")
            if not isinstance(event, dict) or set(event) != {
                "task_id", "from", "to", "agent", "evidence", "passed", "human_approved"
            }:
                raise IntegrityError("Invalid event shape")
            if event["task_id"] != task_id or event["from"] != state.value:
                raise IntegrityError("Unexpected transition source")
            if state in (State.DONE, State.BLOCKED):
                raise IntegrityError("Transition after terminal state")
            if event["agent"] != AGENTS[state]:
                raise IntegrityError("Wrong agent")
            if not isinstance(event["evidence"], str) or not event["evidence"].strip():
                raise IntegrityError("Missing evidence")
            if type(event["passed"]) is not bool:
                raise IntegrityError("Invalid pass result")
            if state == State.INTEGRATE:
                if type(event["human_approved"]) is not bool:
                    raise IntegrityError("Missing approval decision")
            elif event["human_approved"] is not None:
                raise IntegrityError("Unexpected approval")
            target = (State.BLOCKED if not event["passed"]
                      else State.BLOCKED if state == State.INTEGRATE and not event["human_approved"]
                      else NEXT[state])
            if event["to"] != target.value:
                raise IntegrityError("Illegal transition")
            state = target
            previous = event_hash
            events.append(event)
        return {"task_id": task_id, "state": state.value, "events": events}

    def record_advance(self, task_id, *, evidence, passed, approved=False):
        """Replay authoritative history, advance once, atomically append by version."""
        snapshot = self.replay(task_id)
        controller = Controller(task_id=task_id, state=State(snapshot["state"]), events=snapshot["events"])
        controller.advance(evidence=evidence, passed=passed, approved=approved)
        self.append(task_id, controller.events[-1], expected_sequence=len(snapshot["events"]))
        return controller.snapshot()
