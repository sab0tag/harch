"""S2-04: scheduled commands."""
from datetime import date, datetime, time, timedelta
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
    today = timezone.localdate()
    run("generate_daily_tasks", "--date", today.isoformat())  # deadline was yesterday 16:00 — a fresh overdue
    assert "overdue notified: 3" in run("check_deadlines")
    assert "overdue notified: 0" in run("check_deadlines")
    assert Notification.objects.filter(user=hospital.nurses[0], title__startswith="Прострочено").count() == 1


# ------------------------------------------------------------------ weekends and guide-only deadlines
FRIDAY, SATURDAY, SUNDAY, MONDAY, TUESDAY = (date(2026, 10, d) for d in (2, 3, 4, 5, 6))


def test_weekend_orders_are_due_on_friday(settings):
    from apps.workflow.definitions import order_deadline

    settings.ORDER_DEADLINE = "12:00"
    for day in (SATURDAY, SUNDAY, MONDAY):
        assert timezone.localtime(order_deadline(day)).strftime("%a %d.%m %H:%M") == "Fri 02.10 12:00"
    assert timezone.localtime(order_deadline(TUESDAY)).strftime("%a %d.%m %H:%M") == "Mon 05.10 12:00"


@pytest.mark.parametrize("today, expected", [
    (date(2026, 10, 1), [FRIDAY]),                    # Thursday night: Friday only
    (FRIDAY, [SATURDAY, SUNDAY, MONDAY]),             # Friday night: the weekend and Monday at once
    (SATURDAY, [SUNDAY, MONDAY]),                     # a catch-up run on Saturday adds nothing new
])
def test_generation_reaches_the_next_working_day(hospital, today, expected):
    import time_machine

    with time_machine.travel(timezone.make_aware(datetime.combine(today, time(0, 5))), tick=False):
        run("generate_daily_tasks")
    assert sorted(set(Task.objects.values_list("service_date", flat=True))) == expected


def test_only_orders_can_be_overdue(hospital):
    from apps.workflow import services as wf
    from apps.workflow.definitions import COOKING, MENU_PLANNING, WRITE_OFF

    yesterday = timezone.localdate() - timedelta(days=1)
    guide_only = [wf.ensure_task(t, yesterday - timedelta(days=1))[0] for t in (MENU_PLANNING, COOKING, WRITE_OFF)]
    assert all(t.deadline < timezone.now() and not t.is_overdue() for t in guide_only)
    assert not Task.objects.filter(pk__in=[t.pk for t in guide_only]).overdue().exists()
    assert "overdue notified: 0" in run("check_deadlines")
