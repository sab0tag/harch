"""Standalone unit tests; no database, Django or external services required."""
import pytest
from tools.autonomy.controller import Controller, State


def test_happy_path_needs_human_approval():
    c = Controller("T-1")
    for state in (State.TRIAGE, State.PLAN, State.EXECUTE, State.VERIFY, State.REVIEW, State.INTEGRATE):
        assert c.advance(evidence=f"evidence:{state.value}", passed=True) == state
    assert c.advance(evidence="human-review:123", passed=True, approved=True) == State.DONE
    assert len(c.events) == 7


def test_integration_without_human_approval_blocks():
    c = Controller("T-2", state=State.INTEGRATE)
    assert c.advance(evidence="ci:passed", passed=True) == State.BLOCKED


def test_failed_validation_blocks():
    c = Controller("T-3", state=State.VERIFY)
    assert c.advance(evidence="pytest:failed", passed=False) == State.BLOCKED


def test_empty_evidence_rejected():
    c = Controller("T-4")
    with pytest.raises(ValueError):
        c.advance(evidence=" ", passed=True)
    assert c.state == State.DISCOVER


def test_terminal_cannot_advance():
    c = Controller("T-5", state=State.DONE)
    with pytest.raises(ValueError):
        c.advance(evidence="e", passed=True)
