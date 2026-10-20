"""Regression tests for the workflow permission entry point."""

import pytest
from django.contrib.auth.models import AnonymousUser

from apps.workflow.services import can


class UnusedTask:
    """Guard must reject invalid users before touching the task."""

    def __getattr__(self, name):
        raise AssertionError(f"Task accessed before user guard: {name}")


@pytest.mark.parametrize(
    "action",
    ["view", "start", "submit", "approve", "reject", "cancel", "recall", "reopen", "unknown"],
)
def test_anonymous_user_cannot_perform_workflow_action(action):
    assert can(AnonymousUser(), UnusedTask(), action) is False


@pytest.mark.parametrize(
    "action",
    ["view", "start", "submit", "approve", "reject", "cancel", "recall", "reopen"],
)
def test_inactive_user_cannot_perform_workflow_action(action):
    class InactiveUser:
        is_authenticated = True
        is_active = False

    assert can(InactiveUser(), UnusedTask(), action) is False
