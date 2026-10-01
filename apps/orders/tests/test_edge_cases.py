"""Scenarios against the chain's logic (review 30.09): late orders, stale days, empty days, browsing far ahead."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import time_machine
from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.catalog.models import Diet
from apps.consumption.models import ConsumptionSnapshotLine
from apps.orders.corrections import department_portions
from apps.orders.models import OrderCorrection
from apps.orders.services import approved_portions
from apps.orders.tests.conftest import DAY, as_user
from apps.orders.tests.test_orders import post_order
from apps.planning.models import DailyPlan
from apps.planning.services import force_planning, submit_plan
from apps.workflow import services as wf
from apps.workflow.definitions import MENU_PLANNING, ORDER_SUBMISSION
from apps.workflow.models import Task, TaskStatus

pytestmark = pytest.mark.django_db
KYIV = ZoneInfo("Europe/Kyiv")
AFTER_DEADLINE = datetime(2026, 9, 30, 23, 0, tzinfo=KYIV)
LATE = "Ендокринологічне"


def order_task(world, name):
    return Task.objects.get(type=ORDER_SUBMISSION, service_date=DAY, department=world.depts[name])


def plan_task():
    return Task.objects.get(type=MENU_PLANNING, service_date=DAY)


def ovd_portions(plan):
    """Portions of one ОВД dish in the day's consumption: base lines plus «додаткова вимога»."""
    first = ConsumptionSnapshotLine.objects.filter(plan=plan, diet_code="ОВД").first()
    return ConsumptionSnapshotLine.objects.filter(
        plan=plan, dish_card_no=first.dish_card_no, product_id=first.product_id, diet_code="ОВД",
    ).aggregate(p=Sum("portions"))["p"]


@pytest.fixture
def forced_plan(client, world):
    """Two departments approved (10 ОВД each), the third late; the dietician forced the plan after the deadline."""
    for name in ("Терапевтичне", "Хірургічне"):
        post_order(as_user(client, world.nurse[name]), world.depts[name], ОВД="10")
        wf.approve(order_task(world, name), world.dietician)
    with time_machine.travel(AFTER_DEADLINE, tick=True):
        plan = force_planning(DAY, world.dietician)
        submit_plan(plan, world.dietician, {})
        yield plan


def late_order(client, world, **portions):
    post_order(as_user(client, world.nurse[LATE]), world.depts[LATE], **portions)
    return wf.approve(order_task(world, LATE), world.dietician)


# ------------------------------------------------------------------ 1. an order approved after the plan left
def test_late_order_after_frozen_plan_goes_into_consumption(client, world, forced_plan):
    wf.approve(plan_task(), world.chef)
    assert ovd_portions(forced_plan) == 20
    late_order(client, world, ОВД="20")
    correction = OrderCorrection.objects.get(reason=OrderCorrection.Reason.LATE_ORDER)
    assert correction.applied_at and correction.from_meal == "breakfast" and correction.department.name == LATE
    assert ovd_portions(forced_plan) == 40
    assert approved_portions(DAY) == {Diet.objects.get(code="ОВД").pk: 20}                # the plan's base
    assert approved_portions(DAY, include_late=True) == {Diet.objects.get(code="ОВД").pk: 40}
    assert department_portions(world.depts[LATE], DAY) == {Diet.objects.get(code="ОВД").pk: 20}  # not twice


def test_late_order_while_plan_with_the_chef_is_added_at_freeze(client, world, forced_plan):
    late_order(client, world, ОВД="20")
    assert OrderCorrection.objects.get(reason=OrderCorrection.Reason.LATE_ORDER).applied_at is None
    wf.approve(plan_task(), world.chef)
    assert ovd_portions(forced_plan) == 40


def test_late_order_then_chef_returns_the_plan_counts_once(client, world, forced_plan):
    late_order(client, world, ОВД="20")
    wf.reject(plan_task(), world.chef, "Перевірте меню")
    submit_plan(forced_plan, world.dietician, {})
    wf.approve(plan_task(), world.chef)
    assert ovd_portions(forced_plan) == 40


def test_late_order_with_a_diet_not_in_the_plan_uses_its_cycle_menu(client, world, forced_plan):
    wf.approve(plan_task(), world.chef)
    post_order(as_user(client, world.nurse[LATE]), world.depts[LATE], N9="5")
    wf.approve(order_task(world, LATE), world.dietician)
    lines = ConsumptionSnapshotLine.objects.filter(plan=forced_plan, diet_code="№9")
    assert lines.exists() and all(l.correction_id for l in lines)            # «додаткова вимога», not the base
    assert not forced_plan.entries.filter(diet__code="№9").exists()          # the approved plan itself is unchanged


def test_late_order_without_patients_changes_nothing(client, world, forced_plan):
    client.force_login(world.nurse[LATE])
    client.post(f"/orders/{world.depts[LATE].pk}/2026-10-01/", {"action": "submit", "no_patients": "on"})
    wf.approve(order_task(world, LATE), world.dietician)
    assert not OrderCorrection.objects.exists() and Task.objects.filter(type=MENU_PLANNING).count() == 1


# ------------------------------------------------------------------ 2. yesterday's order approved this morning
def test_stale_submitted_order_is_closed_at_night_and_opens_no_plan(client, world):
    post_order(as_user(client, world.nurse["Терапевтичне"]), world.depts["Терапевтичне"], ОВД="10")
    for name in ("Хірургічне", LATE):
        wf.ensure_task(ORDER_SUBMISSION, DAY, department=world.depts[name])
    with time_machine.travel(datetime(2026, 10, 2, 0, 30, tzinfo=KYIV), tick=True):
        assert len(wf.expire_stale_tasks()) == 3
    task = order_task(world, "Терапевтичне")
    assert task.status == TaskStatus.CANCELLED and "не розглянуто" in task.events.last().comment
    assert not Task.objects.filter(type=MENU_PLANNING).exists()


# ------------------------------------------------------------------ 3. nobody has patients
def test_no_portions_anywhere_opens_no_plan(client, world):
    for name, dept in world.depts.items():
        client.force_login(world.nurse[name])
        client.post(f"/orders/{dept.pk}/2026-10-01/", {"action": "submit", "no_patients": "on"})
        wf.approve(order_task(world, name), world.dietician)
    assert not Task.objects.filter(type=MENU_PLANNING).exists()
    with time_machine.travel(AFTER_DEADLINE, tick=True):
        html = as_user(client, world.dietician).post(
            "/orders/summary/", {"date": "2026-10-01", "action": "force_plan"}, follow=True).content.decode()
    assert "план не потрібен" in html and not DailyPlan.objects.exists()
    html = as_user(client, world.admin).get("/tasks/day/?date=2026-09-30").content.decode()
    assert "порцій немає — план не потрібен" in html


# ------------------------------------------------------------------ 4. browsing far ahead
def test_browsing_far_ahead_creates_no_task(client, world):
    ther = world.depts["Терапевтичне"]
    c = as_user(client, world.nurse["Терапевтичне"])
    html = c.get(f"/orders/{ther.pk}/2026-10-07/").content.decode()     # 8 days from 29.09
    assert "Ще рано" in html and "відкриється 30.09" in html
    assert not Task.objects.filter(service_date="2026-10-07").exists()
    assert c.post(f"/orders/{ther.pk}/2026-10-07/", {"action": "save"}).status_code == 404
    c.get(f"/orders/{ther.pk}/2026-10-06/")                                # 7 days: opens
    assert Task.objects.filter(service_date="2026-10-06", department=ther).exists()
    as_user(client, world.dietician).post("/orders/summary/", {"date": "2026-10-07", "action": "create_tasks"})
    assert not Task.objects.filter(service_date="2026-10-07").exists()


# ------------------------------------------------------------------ 5. departments switched on and off
def test_switching_off_the_missing_department_opens_the_plan(client, world):
    for name in ("Терапевтичне", "Хірургічне"):
        post_order(as_user(client, world.nurse[name]), world.depts[name], ОВД="10")
        wf.approve(order_task(world, name), world.dietician)
    assert not Task.objects.filter(type=MENU_PLANNING).exists()      # waits for Ендокринологічне
    endo = world.depts[LATE]
    endo.is_active = False
    endo.save()
    assert plan_task().status == TaskStatus.PENDING


def test_switching_on_a_department_joins_the_days_still_collecting(client, world):
    from apps.orgstructure.models import Department

    post_order(as_user(client, world.nurse["Терапевтичне"]), world.depts["Терапевтичне"], ОВД="10")
    new = Department.objects.filter(is_active=False).first()
    new.is_active = True
    new.save()
    assert Task.objects.filter(type=ORDER_SUBMISSION, service_date=DAY, department=new).exists()   # deadline ahead
    with time_machine.travel(AFTER_DEADLINE, tick=True):     # too late for 01.10: not added, the plan is forced
        late = Department.objects.filter(is_active=False).first()
        late.is_active = True
        late.save()
    assert not Task.objects.filter(type=ORDER_SUBMISSION, service_date=DAY, department=late).exists()


# ------------------------------------------------------------------ 7. abandoned plans of past days
def test_untouched_past_plan_is_closed_after_three_days(client, world, forced_plan):
    with time_machine.travel(datetime(2026, 10, 3, 1, 0, tzinfo=KYIV), tick=True):
        assert plan_task() not in wf.expire_stale_tasks()        # day over, but touched 2 days ago
    with time_machine.travel(datetime(2026, 10, 4, 1, 0, tzinfo=KYIV), tick=True):
        wf.expire_stale_tasks()
    task = plan_task()
    assert task.status == TaskStatus.CANCELLED and "план не чіпали 3 дні" in task.events.last().comment


def test_late_order_reason_is_not_offered_to_the_nurse(client, world, forced_plan):
    from apps.orders.corrections import open_correction, save_correction

    wf.approve(plan_task(), world.chef)
    nurse = world.nurse["Терапевтичне"]
    correction = open_correction(world.depts["Терапевтичне"], DAY, nurse)
    html = as_user(client, nurse).get(f"/orders/corrections/{correction.pk}/").content.decode()
    assert "Надходження" in html and "Пізнє замовлення" not in html
    with pytest.raises(ValidationError, match="Оберіть причину"):
        save_correction(correction, nurse, {Diet.objects.get(code="ОВД").pk: 1}, from_meal="dinner",
                        reason=OrderCorrection.Reason.LATE_ORDER)


# ------------------------------------------------------------------ 6. a correction with a diet the day lacks
def test_correction_adds_a_diet_nobody_else_has(client, world, forced_plan):
    from apps.orders.corrections import open_correction, submit_correction

    wf.approve(plan_task(), world.chef)                       # the day cooks ОВД only
    nurse = world.nurse["Хірургічне"]
    correction = open_correction(world.depts["Хірургічне"], DAY, nurse)
    html = as_user(client, nurse).get(f"/orders/corrections/{correction.pk}/").content.decode()
    assert "у плані дня немає — готуватимуть за меню №" in html
    submit_correction(correction, nurse, {Diet.objects.get(code="№9").pk: 2}, from_meal="lunch",
                      reason=OrderCorrection.Reason.ADMISSION)
    wf.approve(correction.task, world.dietician)
    lines = ConsumptionSnapshotLine.objects.filter(plan=forced_plan, correction=correction)
    assert lines.exists() and {l.diet_code for l in lines} == {"№9"}
    assert {l.meal_slot for l in lines} <= {"lunch", "afternoon_snack", "dinner"}


# ------------------------------------------------------------------ admin catches up a closed past day
def test_admin_reopens_an_auto_closed_past_order(client, world):
    ther = world.depts["Терапевтичне"]
    task = wf.ensure_task(ORDER_SUBMISSION, DAY, department=ther)[0]
    with time_machine.travel(datetime(2026, 10, 2, 1, 0, tzinfo=KYIV), tick=True):
        wf.expire_stale_tasks()
        task.refresh_from_db()
        assert task.status == TaskStatus.CANCELLED
        url = f"/orders/{ther.pk}/2026-10-01/"
        assert "Відкрити заново" not in as_user(client, world.nurse["Терапевтичне"]).get(url).content.decode()
        client.post(url, {"action": "reopen"})
        task.refresh_from_db()
        assert task.status == TaskStatus.CANCELLED                             # the nurse cannot
        admin = as_user(client, world.admin)
        assert "Відкрити заново" in admin.get(url).content.decode()
        admin.post(url, {"action": "reopen"})
        task.refresh_from_db()
        assert task.status == TaskStatus.PENDING and task.events.last().kind == "reopened"
        assert task not in wf.todo_for(world.nurse["Терапевтичне"])            # not handed back to the nurse
        admin.post(url, {"action": "submit", f"diet_{Diet.objects.get(code='ОВД').pk}": "7"})
        task.refresh_from_db()
        assert task.status == TaskStatus.SUBMITTED
    with time_machine.travel(datetime(2026, 10, 3, 1, 0, tzinfo=KYIV), tick=True):
        assert task not in wf.expire_stale_tasks()                             # a reopened day gets 3 days
    with time_machine.travel(datetime(2026, 10, 6, 1, 0, tzinfo=KYIV), tick=True):
        assert task in wf.expire_stale_tasks()


def test_presentation_link_only_on_the_demo(client, settings):
    settings.DEMO_MODE = False
    assert "Презентація системи" not in client.get("/login/").content.decode()
    settings.DEMO_MODE = True
    html = client.get("/login/").content.decode()
    assert "Презентація системи" in html and settings.DEMO_PRESENTATION_URL in html
