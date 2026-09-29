"""Past service dates: a nurse only looks, an admin may catch a day up, stale orders close at night.

"Today" is pinned to 29.09.2026 (root conftest): PAST = 26.09, TOMORROW = 30.09.
"""
from datetime import date, timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.orders.models import DailyOrder
from apps.orders.tests.conftest import as_user
from apps.workflow import services as wf
from apps.workflow.definitions import MENU_PLANNING, ORDER_SUBMISSION
from apps.workflow.models import Notification, Task, TaskStatus

pytestmark = pytest.mark.django_db
PAST, TODAY, TOMORROW = date(2026, 9, 26), date(2026, 9, 29), date(2026, 9, 30)


def url(dept, day):
    return f"/orders/{dept.pk}/{day.isoformat()}/"


@pytest.fixture
def ther(world):
    return world.depts["Терапевтичне"]


def test_today_is_pinned():
    assert timezone.localdate() == TODAY


def test_browsing_a_past_day_creates_nothing(client, world, ther):
    html = as_user(client, world.nurse["Терапевтичне"]).get(url(ther, PAST)).content.decode()
    assert "Замовлення на цю дату не подавалось" in html and "Зведення замовлень" not in html
    assert not Task.objects.filter(service_date=PAST).exists() and not DailyOrder.objects.exists()


@pytest.mark.parametrize("day", [TODAY, TOMORROW])
def test_today_and_later_still_open_for_the_nurse(client, world, ther, day):
    html = as_user(client, world.nurse["Терапевтичне"]).get(url(ther, day)).content.decode()
    assert "Подати" in html and Task.objects.filter(service_date=day, department=ther).exists()


def test_an_open_past_order_is_read_only_for_the_nurse(client, world, ther):
    from apps.orders.services import open_order

    nurse = as_user(client, world.nurse["Терапевтичне"])
    wf.ensure_task(ORDER_SUBMISSION, PAST, department=ther)                 # task, never opened
    html = nurse.get(url(ther, PAST)).content.decode()
    assert "Замовлення на цю дату не подавалось" in html and 'value="submit"' not in html
    open_order(ther, PAST)                                                   # a draft left unsubmitted
    html = nurse.get(url(ther, PAST)).content.decode()
    assert "Дата минула — замовлення не подано" in html and 'value="submit"' not in html
    from apps.catalog.models import Diet

    r = nurse.post(url(ther, PAST), {"action": "submit", f"diet_{Diet.objects.get(code='ОВД').pk}": "5"}, follow=True)
    assert "Дата минула — замовлення можна лише переглянути" in r.content.decode()
    task = Task.objects.get(service_date=PAST, department=ther)
    assert task not in wf.todo_for(world.nurse["Терапевтичне"])            # not in «Мені виконати»
    nurse.post(f"/tasks/{task.pk}/submit/")                                 # the generic route is closed too
    task.refresh_from_db()
    assert task.status == TaskStatus.PENDING


def test_admin_can_catch_up_a_missed_day(client, world, ther):
    admin = as_user(client, world.admin)
    html = admin.get(url(ther, PAST)).content.decode()
    assert 'value="submit"' in html
    from apps.catalog.models import Diet

    admin.post(url(ther, PAST), {"action": "submit", f"diet_{Diet.objects.get(code='ОВД').pk}": "7"})
    assert Task.objects.get(service_date=PAST, department=ther).status == TaskStatus.SUBMITTED


def test_dietician_gets_no_creation_buttons_for_a_past_day(client, world):
    diet = as_user(client, world.dietician)
    html = diet.get(f"/orders/summary/?date={PAST.isoformat()}").content.decode()
    assert "Дата минула" in html and "create_tasks" not in html
    diet.post("/orders/summary/", {"date": PAST.isoformat(), "action": "create_tasks"})
    assert not Task.objects.filter(service_date=PAST).exists()
    as_user(client, world.admin).post("/orders/summary/", {"date": PAST.isoformat(), "action": "create_tasks"})
    assert Task.objects.filter(service_date=PAST).count() == 3              # admin: deliberate catch-up


def test_nightly_expiry_closes_only_past_unfilled_orders(world, ther):
    surgery, endo = world.depts["Хірургічне"], world.depts["Ендокринологічне"]
    stale, _ = wf.ensure_task(ORDER_SUBMISSION, PAST, department=ther)
    submitted, _ = wf.ensure_task(ORDER_SUBMISSION, PAST, department=surgery)
    submitted.status = TaskStatus.SUBMITTED
    submitted.save()
    today, _ = wf.ensure_task(ORDER_SUBMISSION, TODAY, department=endo)
    Notification.objects.all().delete()

    out = StringIO()
    call_command("expire_stale_tasks", "--dry-run", stdout=out)
    assert "буде закрито" in out.getvalue() and Task.objects.get(pk=stale.pk).status == TaskStatus.PENDING
    call_command("expire_stale_tasks", stdout=StringIO())

    stale.refresh_from_db(), submitted.refresh_from_db(), today.refresh_from_db()
    assert stale.status == TaskStatus.CANCELLED
    assert stale.events.last().comment == "Дата минула — закрито автоматично"
    assert submitted.status == TaskStatus.SUBMITTED and today.status == TaskStatus.PENDING
    assert not Notification.objects.exists()                                # quietly
    assert not Task.objects.filter(type=MENU_PLANNING, service_date=PAST).exists()  # no hook for a finished day


def test_only_fresh_overdues_are_announced(world, ther):
    old, _ = wf.ensure_task(ORDER_SUBMISSION, PAST, department=ther)         # deadline 25.09 16:00: days ago
    fresh, _ = wf.ensure_task(ORDER_SUBMISSION, TODAY, department=ther)      # deadline 28.09 16:00: < 24 h
    notified = wf.notify_overdue(now=timezone.now())
    assert fresh in notified and old not in notified
    assert Task.objects.get(pk=old.pk).overdue_notified_at is None


def test_demo_history_is_entered_as_admin_catch_up(settings):
    settings.DEBUG = True
    call_command("load_seed", "seed", verbosity=0, stdout=StringIO())
    call_command("demo_days", days=1, stdout=StringIO())
    yesterday = TODAY - timedelta(days=1)
    orders = Task.objects.filter(type=ORDER_SUBMISSION, service_date=yesterday)
    assert orders.exists() and all(t.status == TaskStatus.APPROVED for t in orders)


# ------------------------------------------------------------------ switched-off departments
def test_a_switched_off_departments_order_stays_readable(client, world, ther):
    """Notifications and history link to it: no 404 after sync_departments switched it off."""
    from apps.catalog.models import Diet
    from apps.workflow.models import Notification

    nurse = world.nurse["Терапевтичне"]
    from apps.orders.services import open_order, submit_order

    submit_order(open_order(ther, TOMORROW), nurse, {Diet.objects.get(code="ОВД").pk: 4})
    task = Task.objects.get(service_date=TOMORROW, department=ther)
    wf.approve(task, world.dietician)
    ther.is_active = False
    ther.save()
    admin = as_user(client, world.admin)
    note = Notification.objects.filter(task=task).first() or Notification.objects.create(
        user=world.admin, task=task, title="Затверджено")
    note.user = world.admin
    note.save()
    r = admin.get(f"/tasks/notifications/{note.pk}/open/", follow=True)
    assert r.status_code == 200 and "відділення вимкнено" in r.content.decode()


def test_nothing_new_is_opened_for_a_switched_off_department(client, world, ther):
    ther.is_active = False
    ther.save()
    html = as_user(client, world.admin).get(url(ther, TOMORROW)).content.decode()
    assert "відділення вимкнено" in html and not Task.objects.filter(department=ther).exists()
