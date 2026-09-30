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


def test_late_order_with_a_diet_not_in_the_plan_is_refused(client, world, forced_plan):
    post_order(as_user(client, world.nurse[LATE]), world.depts[LATE], N9="5")
    with pytest.raises(ValidationError, match="немає дієт: №9"):
        wf.approve(order_task(world, LATE), world.dietician)
    assert order_task(world, LATE).status == TaskStatus.SUBMITTED
    assert not OrderCorrection.objects.exists()


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
