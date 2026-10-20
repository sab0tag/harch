# v11 — durable Mission Control event store

SQLite append-only events keyed by (task_id, sequence), optimistic concurrency and
SHA-256 chained integrity checks. Replay reconstructs snapshots accepted by the
v10 static read-only Mission Control renderer.

Example (local only):

```python
from pathlib import Path
from tools.autonomy.event_store import EventStore
from tools.autonomy.mission_control import export_html

store = EventStore("autonomy-events.sqlite3")
store.record_advance("RUN-001", evidence="manual:discovery", passed=True)
export_html(store.replay("RUN-001"), Path("mission-control.html"))
```

Tests: `pytest tests/test_autonomy_controller.py tests/test_autonomy_mission_control.py tests/test_autonomy_event_store.py -q`

**Limitations:** hash chaining detects accidental modification but is **not
cryptographic authenticity**: a writer with database access can rewrite the chain.
There is no trusted identity for approvals, no actual agents, no immutable external
audit log, no live streaming, no event subscription and no deployment integration.
Do not treat the caller-supplied `approved=True` as real human authorization.
This must remain isolated from hospital production operations.
