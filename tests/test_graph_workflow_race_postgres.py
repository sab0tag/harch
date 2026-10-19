"""Real PostgreSQL concurrency regression: only one approval may commit.

Uses two database connections (one per thread). Never run against production.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections
from django.db import connection

from apps.workflow.models import Task, TaskEvent, TaskStatus
from apps.workflow.services import InvalidTransition, approve
from apps.workflow.registry import TaskDefinition, register, unregister


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_approvals_produce_one_event(monkeypatch):
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row locks")

    from apps.workflow import services

    User = get_user_model()
    maker = User.objects.create_user(username="race-maker", password="test-only", role="dietician")
    checker_a = User.objects.create_user(username="race-checker-a", password="test-only", role="dietician")
    checker_b = User.objects.create_user(username="race-checker-b", password="test-only", role="dietician")

    register(TaskDefinition(type="graph_race_test", title="Race test", maker_role="dietician", checker_role="dietician", deadline=lambda day: None))
    task = Task.objects.create(
        type="graph_race_test",
        service_date=date(2030, 1, 1),
        status=TaskStatus.SUBMITTED,
        maker_role="dietician",
        checker_role="dietician",
        submitted_by=maker,
    )

    # This test is about transaction serialization, not downstream notifications
    # or side effects that depend on the task's domain subject.
    monkeypatch.setattr(services, "n", type("Notifications", (), {"notify": staticmethod(lambda *a, **kw: None)})())
    monkeypatch.setattr(services, "_substitution_mark", lambda task, user, comment: comment)
    monkeypatch.setattr(services, "_admin_mark", lambda user, verb, comment: comment)

    barrier = Barrier(2)

    def attempt(user_pk):
        close_old_connections()
        try:
            user = User.objects.get(pk=user_pk)
            barrier.wait(timeout=10)
            try:
                approve(Task.objects.get(pk=task.pk), user)
                return "approved"
            except (InvalidTransition, PermissionDenied):
                return "rejected"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (checker_a.pk, checker_b.pk)))

    task.refresh_from_db()
    assert sorted(outcomes) == ["approved", "rejected"]
    assert task.status == TaskStatus.APPROVED
    assert TaskEvent.objects.filter(task=task, kind=TaskEvent.Kind.APPROVED).count() == 1
    unregister("graph_race_test")
