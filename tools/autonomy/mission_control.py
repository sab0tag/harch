"""Read-only Mission Control exporter for trusted controller snapshots.

No Django, LLM, GitHub or business workflow dependencies.
"""
import html
import json
from pathlib import Path

from .controller import State


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("task_id"), str):
        raise ValueError("Invalid snapshot")
    if not snapshot["task_id"] or len(snapshot["task_id"]) > 128:
        raise ValueError("Invalid task id")
    if snapshot.get("state") not in {state.value for state in State}:
        raise ValueError("Invalid state")
    events = snapshot.get("events")
    if not isinstance(events, list) or len(events) > 1000:
        raise ValueError("Invalid events")
    required = {"task_id", "from", "to", "agent", "evidence", "passed", "human_approved"}
    for event in events:
        if not isinstance(event, dict) or not required.issubset(event):
            raise ValueError("Invalid event")
        if event["task_id"] != snapshot["task_id"]:
            raise ValueError("Cross-task event")
        if event["from"] not in {s.value for s in State} or event["to"] not in {s.value for s in State}:
            raise ValueError("Invalid event transition")
        if not isinstance(event["evidence"], str) or len(event["evidence"]) > 500:
            raise ValueError("Invalid evidence")
    return snapshot


def render_html(snapshot, *, mode="snapshot"):
    """Static, CSP-restricted HTML. No active controls or remote dependencies."""
    validate_snapshot(snapshot)
    if mode not in ("snapshot", "demo"):
        raise ValueError("Invalid mode")
    esc = lambda v: html.escape(str(v), quote=True)
    steps = [s.value for s in State if s not in (State.BLOCKED, State.DONE)]
    state = snapshot["state"]
    cards = "".join(
        '<div class="node ' + ('current' if name == state else 'done' if name in [e["from"] for e in snapshot["events"]] else '') + '"><small>STAGE</small><strong>' + esc(name) + '</strong></div>'
        for name in steps
    )
    rows = "".join('<tr><td>' + str(i) + '</td><td>' + esc(e["agent"]) + '</td><td>' + esc(e["from"]) + ' → ' + esc(e["to"]) + '</td><td>' + esc(e["evidence"]) + '</td></tr>' for i, e in enumerate(snapshot["events"], 1))
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>HARCH Mission Control</title><style>
:root{color-scheme:dark}body{font:15px system-ui;background:#090e1a;color:#e7edf8;margin:0;padding:32px}main{max-width:1080px;margin:auto}header{display:flex;justify-content:space-between;align-items:center;gap:20px}h1{font-size:27px;letter-spacing:.06em}h2{font-size:18px;margin-top:36px}.tag{background:#1e293b;border:1px solid #52617c;border-radius:30px;padding:9px 15px;font-size:12px}.graph{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}.node{padding:20px;background:#151d2d;border:1px solid #28364d;border-radius:14px;min-height:66px}.node small{display:block;color:#8494ae;margin-bottom:13px}.node strong{font-size:13px;letter-spacing:.05em}.node.done{border-color:#268a71}.node.current{background:#163c58;border-color:#56b8fa}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;border-bottom:1px solid #29364a;padding:12px;word-break:break-word}th{color:#9babc2}section{overflow:auto}footer{color:#8290a6;font-size:12px;margin-top:40px}
</style><main><header><div><small>HARCH / ENGINEERING</small><h1>MISSION CONTROL</h1></div><span class="tag">''' + esc(mode.upper()) + ''' · READ ONLY</span></header><p>Task: <strong>''' + esc(snapshot["task_id"]) + '''</strong> · Current state: <strong>''' + esc(state) + '''</strong></p><h2>Execution graph</h2><div class="graph">''' + cards + '''</div><h2>Evidence timeline</h2><section><table><thead><tr><th>#</th><th>Agent</th><th>Transition</th><th>Evidence reference</th></tr></thead><tbody>''' + rows + '''</tbody></table></section><footer>Static snapshot. Not a live agent connection. Evidence references are unverified labels, not test attestations. No actions can be performed from this page.</footer></main></html>'''


def export_html(snapshot, output: Path, *, mode="snapshot"):
    output = Path(output)
    output.write_text(render_html(snapshot, mode=mode), encoding="utf-8")
    return output
