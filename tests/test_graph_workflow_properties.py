"""Property-based checks for the maker/checker permission invariant."""
from types import SimpleNamespace
from unittest.mock import patch

from hypothesis import given, settings, strategies as st

from apps.workflow.models import TaskStatus
from apps.workflow.services import can


@given(
    user_id=st.integers(min_value=1, max_value=100000),
    checker_id=st.one_of(st.none(), st.integers(min_value=1, max_value=100000)),
    role=st.sampled_from(["admin", "dept_nurse", "dietician", "chef", "accountant", "storekeeper"]),
)
@settings(max_examples=250, deadline=None)
def test_submitter_cannot_approve_even_if_assigned_checker(user_id, checker_id, role):
    """Any submitted task must reject approval by its own submitter."""
    user = SimpleNamespace(pk=user_id, role=role)
    task = SimpleNamespace(
        status=TaskStatus.SUBMITTED, submitted_by_id=user_id,
        checker_id=checker_id, checker_role=role,
    )
    with patch("apps.workflow.services.is_checker", return_value=True):
        assert can(user, task, "approve") is False


@given(
    status=st.sampled_from([
        TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.REJECTED,
        TaskStatus.APPROVED, TaskStatus.CANCELLED,
    ]),
    user_id=st.integers(min_value=1, max_value=100000),
)
@settings(max_examples=150, deadline=None)
def test_only_submitted_tasks_are_approvable(status, user_id):
    user = SimpleNamespace(pk=user_id, role="dietician")
    task = SimpleNamespace(status=status, submitted_by_id=user_id + 1)
    with patch("apps.workflow.services.is_checker", return_value=True):
        assert can(user, task, "approve") is False
