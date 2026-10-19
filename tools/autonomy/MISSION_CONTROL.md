# Mission Control v10

Read-only static dashboard for engineering-controller snapshots.

Generate a **synthetic** example from repository root:

```bash
python -m tools.autonomy.demo
```

Open `mission-control-demo.html` locally. It has no scripts, remote resources or
interactive controls. To render an actual controller snapshot, call
`export_html(controller.snapshot(), Path("mission-control.html"))`.

Tests: `pytest tests/test_autonomy_controller.py tests/test_autonomy_mission_control.py -q`.

## Non-goals / limitations
- No live stream, database persistence, agents or browser automation.
- The demo timeline is fabricated and clearly marked DEMO.
- Snapshot events are caller-provided and evidence labels are not verified attestations.
- Human approval in v9 is still a caller-supplied boolean, not authenticated.
- Never host this on the production hospital site without security review.
- Future: signed/durable event store, authenticated read-only API, live graph and replay.
