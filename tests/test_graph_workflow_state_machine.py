"""State-machine regressions for real workflow transitions and failed-action atomicity."""
from datetime import date
from dataclasses import replace

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import Role
from apps.workflow import services as wf
from apps.workflow.models import Notification, Task, TaskEvent, TaskStatus
from apps.workflow.registry import TaskDefinition, register, unregister


@pytest.fixture
def scenario(monkeypatch):
    task_type = "graph_state_regression"
    register(TaskDefinition(
        type=task_type, title="State regression",
        maker_role=Role.DIETICIAN, checker_role=Role.CHEF,
        deadline=lambda day: None,
    ))
    monkeypatch.setattr(wf.n, "notify", lambda *args, **kwargs: None)
    User = get_user_model()
    maker = User.objects.create_user(username="state-maker", password="test-only", role=Role.DIETICIAN)
    checker = User.objects.create_user(username="state-checker", password="test-only", role=Role.CHEF)
    outsider = User.objects.create_user(username="state-outsider", password="test-only", role=Role.ACCOUNTANT)
    task = Task.objects.create(
        type=task_type, service_date=date(2031, 2, 1),
        maker_role=Role.DIETICIAN, checker_role=Role.CHEF,
    )
    yield task, maker, checker, outsider
    unregister(task_type)


def snapshot(task):
    task.refresh_from_db()
    return (
        task.status, task.started_at, task.submitted_at, task.submitted_by_id,
        task.resolved_at, task.resolved_by_id, task.reject_count,
        list(TaskEvent.objects.filter(task=task).values_list(
            "kind", "from_status", "to_status", "actor_id", "comment"
        )),
        list(Notification.objects.filter(task=task).values_list("pk", "user_id", "title")),
    )


@pytest.mark.django_db
def test_reject_then_resubmit_then_approve_and_double_actions(scenario):
    task, maker, checker, outsider = scenario
    wf.start(task, maker)
    wf.start(task, maker)  # idempotent: no second STARTED event
    assert TaskEvent.objects.filter(task=task, kind=TaskEvent.Kind.STARTED).count() == 1

    wf.submit(task, maker)
    before = snapshot(task)
    with pytest.raises(PermissionDenied):
        wf.approve(task, maker)
    assert snapshot(task) == before
    with pytest.raises(wf.InvalidTransition):
        wf.submit(task, maker)
    assert snapshot(task) == before
    with pytest.raises(ValidationError):
        wf.reject(task, checker, "  ")
    assert snapshot(task) == before

    wf.reject(task, checker, "Please revise")
    assert task.pk is not None
    task.refresh_from_db()
    assert task.status == TaskStatus.REJECTED
    assert task.reject_count == 1
    wf.submit(task, maker, "Revised")
    wf.approve(task, checker)
    assert snapshot(task)[0] == TaskStatus.APPROVED

    before = snapshot(task)
    for action, user in ((wf.approve, checker), (wf.recall, maker), (wf.submit, maker)):
        with pytest.raises(wf.InvalidTransition):
            action(task, user)
        assert snapshot(task) == before


@pytest.mark.django_db
def test_recall_resubmit_and_denied_checker_action(scenario):
    task, maker, checker, outsider = scenario
    wf.submit(task, maker)
    before = snapshot(task)
    with pytest.raises(PermissionDenied):
        wf.reject(task, outsider, "Not my task")
    assert snapshot(task) == before

    wf.recall(task, maker, "Update")
    task.refresh_from_db()
    assert task.status == TaskStatus.IN_PROGRESS
    before = snapshot(task)
    with pytest.raises(wf.InvalidTransition):
        wf.recall(task, maker)
    assert snapshot(task) == before

    wf.submit(task, maker)
    wf.approve(task, checker)
    assert TaskEvent.objects.filter(task=task, kind=TaskEvent.Kind.SUBMITTED).count() == 2
    assert TaskEvent.objects.filter(task=task, kind=TaskEvent.Kind.RECALLED).count() == 1
    assert TaskEvent.objects.filter(task=task, kind=TaskEvent.Kind.APPROVED).count() == 1


@pytest.mark.django_db
def test_submit_hook_failure_rolls_back_state_and_events(scenario, monkeypatch):
    task, maker, checker, outsider = scenario
    before = snapshot(task)

    def fail_hook(_task):
        raise RuntimeError("simulated downstream failure")

    register(replace(task.definition, on_submitted=fail_hook))
    with pytest.raises(RuntimeError, match="simulated downstream failure"):
        wf.submit(task, maker)
    assert snapshot(task) == before
