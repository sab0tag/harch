"""Behavioral authorization regression tests; run with pytest-django."""
from types import SimpleNamespace
from unittest.mock import patch

from apps.workflow.services import can
from apps.workflow.models import TaskStatus


def test_approval_rejects_maker_even_when_checker():
    user = SimpleNamespace(pk=7, role="dietician", is_authenticated=True, is_active=True)
    task = SimpleNamespace(status=TaskStatus.SUBMITTED, submitted_by_id=7,
                           checker_id=7, maker_id=7, checker_role="dietician")
    assert not can(user, task, "approve")


def test_admin_cannot_approve_by_default():
    user = SimpleNamespace(pk=9, role="admin", is_authenticated=True, is_active=True)
    task = SimpleNamespace(status=TaskStatus.SUBMITTED, submitted_by_id=7,
                           checker_id=None, maker_id=7, checker_role="dietician")
    with patch("apps.workflow.services.settings.ADMIN_CAN_APPROVE", False):
        assert not can(user, task, "approve")
