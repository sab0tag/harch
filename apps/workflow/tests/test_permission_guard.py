"""can() denies anonymous and inactive users before any role check (real models, real task).

Regression: for an admin can() looked at the role only, so an inactive admin could still view, cancel
and reopen; an anonymous user made can(…, "view") raise AttributeError instead of returning False.
"""
import pytest
from django.contrib.auth.models import AnonymousUser

from apps.accounts.models import Role
from apps.accounts.tests.factories import UserFactory
from apps.workflow import services as wf
from apps.workflow.models import TaskStatus
from apps.workflow.tests.test_services import orders

pytestmark = pytest.mark.django_db
ACTIONS = ("view", "start", "submit", "approve", "reject", "cancel", "recall", "reopen")


@pytest.mark.parametrize("action", ACTIONS)
def test_inactive_admin_is_denied(hospital, action):
    task = orders(hospital)[0]
    admin = UserFactory(role=Role.ADMIN, is_active=False)
    assert wf.can(admin, task, action) is False


@pytest.mark.parametrize("action", ACTIONS)
def test_anonymous_is_denied_without_raising(hospital, action):
    assert wf.can(AnonymousUser(), orders(hospital)[0], action) is False


def test_active_users_keep_their_rights(hospital):
    """The guard changes nothing for active users: admin sees and cancels, the nurse submits her own."""
    task = orders(hospital)[0]
    admin = UserFactory(role=Role.ADMIN)
    assert wf.can(admin, task, "view") and wf.can(admin, task, "cancel")
    assert task.status == TaskStatus.PENDING and wf.can(hospital.nurses[0], task, "submit")
