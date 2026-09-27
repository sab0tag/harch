"""S2-04: scheduled commands."""
from datetime import date, timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.orgstructure.models import Department
from apps.workflow.definitions import ORDER_SUBMISSION
from apps.workflow.models import Notification, Task

pytestmark = pytest.mark.django_db


def run(*args):
    out = StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


def test_generate_for_date_one_task_per_active_department(hospital):
    Department.objects.create(name="Закрите", is_active=False)
    out = run("generate_daily_tasks", "--date", "2026-10-01")
    assert "created 3" in out
    assert Task.objects.filter(type=ORDER_SUBMISSION, service_date=date(2026, 10, 1)).count() == 3


def test_generate_is_idempotent(hospital):
    run("generate_daily_tasks", "--date", "2026-10-01")
    assert "created 0" in run("generate_daily_tasks", "--date", "2026-10-01")
    assert Task.objects.count() == 3


def test_generate_days_ahead_starts_tomorrow(hospital):
    run("generate_daily_tasks", "--days-ahead", "2")
    today = timezone.localdate()
    assert set(Task.objects.values_list("service_date", flat=True)) == {today + timedelta(1), today + timedelta(2)}


def test_generate_bad_args(hospital):
    with pytest.raises(CommandError):
        run("generate_daily_tasks", "--date", "01.10.2026")
    with pytest.raises(CommandError):
        run("generate_daily_tasks", "--days-ahead", "0")


def test_check_deadlines_notifies_overdue(hospital):
    yesterday = timezone.localdate() - timedelta(days=1)
    run("generate_daily_tasks", "--date", yesterday.isoformat())  # deadline was the day before yesterday
    assert "overdue notified: 3" in run("check_deadlines")
    assert "overdue notified: 0" in run("check_deadlines")
    assert Notification.objects.filter(user=hospital.nurses[0], title__startswith="Прострочено").count() == 1
