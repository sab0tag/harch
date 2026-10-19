"""Mission Control renderer tests; no browser or Django required."""
import pytest
from tools.autonomy.controller import Controller, State
from tools.autonomy.mission_control import render_html, validate_snapshot


def test_renders_current_state_and_evidence():
    c = Controller("RUN-1")
    c.advance(evidence="ci:123", passed=True)
    page = render_html(c.snapshot())
    assert "RUN-1" in page and "TRIAGE" in page and "ci:123" in page
    assert "READ ONLY" in page


def test_html_escapes_untrusted_evidence():
    c = Controller("RUN-2")
    c.advance(evidence="<script>alert(1)</script>", passed=True)
    page = render_html(c.snapshot())
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_rejects_cross_task_event():
    c = Controller("RUN-3")
    c.advance(evidence="ci:1", passed=True)
    snap = c.snapshot()
    snap["events"][0]["task_id"] = "OTHER"
    with pytest.raises(ValueError):
        validate_snapshot(snap)


def test_rejects_unknown_state():
    with pytest.raises(ValueError):
        render_html({"task_id": "T", "state": "MAGIC", "events": []})


def test_demo_label():
    assert "DEMO · READ ONLY" in render_html(Controller("DEMO").snapshot(), mode="demo")
