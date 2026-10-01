"""S7-01: order corrections — «добавка до вимоги» after the order is approved.

"Today" is pinned to 29.09.2026 10:00 (root conftest); DAY = 01.10.2026 (a future day: every meal allowed).
"""
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
import time_machine
from django.core.exceptions import ValidationError

from apps.consumption.models import ConsumptionSnapshotLine
from apps.consumption.services import ensure_actuals, kitchen_board, live_write_off_rows, snapshot_totals
from apps.consumption.tests.conftest import cooking_day, diet, write_off_day, world  # noqa: F401
from apps.orders import corrections
from apps.orders.models import OrderCorrection
from apps.orders.services import open_order, submit_order
from apps.orders.tests.conftest import DAY, as_user
from apps.planning.services import open_plan
from apps.reports.exports import menu_vymoha_data
from apps.reports.pdf import menu_rozkladka_context
from apps.workflow import services as wf
from apps.workflow.definitions import MENU_PLANNING, ORDER_CORRECTION
from apps.workflow.models import Notification, Task, TaskStatus

pytestmark = pytest.mark.django_db


def correct(w, dept_name, deltas, *, from_meal="lunch", reason="admission", transfer_from=None, approve=True):
    nurse = w.nurse[dept_name]
    c = corrections.open_correction(w.depts[dept_name], DAY, nurse)
    corrections.submit_correction(c, nurse, {diet(k).pk: v for k, v in deltas.items()}, from_meal=from_meal,
                                  reason=reason, transfer_from=w.depts.get(transfer_from))
    if approve:
        wf.approve(c.task, w.dietician)
    c.refresh_from_db()
    return c


def extra_brutto(c):
    return sum(l.brutto_total_g for l in ConsumptionSnapshotLine.objects.filter(correction=c))


def test_admission_after_the_freeze_becomes_an_extra_requirement(cooking_day):
    w = cooking_day
    base_board = {(s["slot"], d["card_no"]): d["portions"] for s in kitchen_board(w.plan) for d in s["dishes"]}
    base_totals = snapshot_totals(w.plan)
    c = correct(w, "Терапевтичне", {"ОВД": 2})

    assert c.applied_at is not None and extra_brutto(c) > 0
    board = {(s["slot"], d["card_no"]): d["portions"] for s in kitchen_board(w.plan) for d in s["dishes"]}
    grew = {k for k in board if board[k] != base_board.get(k)}
    assert grew and all(slot in ("lunch", "afternoon_snack", "dinner") for slot, _ in grew)  # from lunch on only
    assert all(board[k] - base_board[k] == 2 for k in grew)
    assert snapshot_totals(w.plan) == base_totals                     # the approved plan document stays as it was
    ctx = menu_rozkladka_context(w.plan)
    assert {d["code"]: d["portions"] for d in ctx["diets"]}["ОВД"] == 32
    assert menu_vymoha_data(w.plan)["eaters"]["ОВД"] == 34            # who eats that day: 32 + 2
    assert Notification.objects.filter(user=w.nurse["Терапевтичне"], title__startswith="Затверджено").exists()


def test_the_write_off_follows_the_kitchen_fact_including_the_correction(cooking_day):
    w = cooking_day
    ensure_actuals(w.plan)                                            # the chef already opened the fact table
    before = sum(r["quantity_g"] for r in live_write_off_rows(w.plan))
    c = correct(w, "Хірургічне", {"ЩД": 3}, from_meal="breakfast")
    after = sum(r["quantity_g"] for r in live_write_off_rows(w.plan))
    assert after - before == extra_brutto(c)


def test_transfer_is_one_action_and_moves_portions(cooking_day):
    w = cooking_day
    c = correct(w, "Хірургічне", {"ОВД": 1}, reason="transfer", transfer_from="Терапевтичне")
    lines = {(l.department.name, l.diet.code): l.delta for l in c.lines.all()}
    assert lines == {("Хірургічне", "ОВД"): 1, ("Терапевтичне", "ОВД"): -1}
    assert extra_brutto(c) == 0                                       # the kitchen cooks the same; only who gets it
    assert corrections.department_portions(w.depts["Терапевтичне"], DAY)[diet("ОВД").pk] == 29
    assert Notification.objects.filter(user=w.nurse["Терапевтичне"], title__startswith="Переведення").exists()


def test_approved_before_the_freeze_is_added_when_the_plan_is_approved(world):
    w = world
    for name, portions in {"Терапевтичне": {"ОВД": 10}, "Хірургічне": {"ЩД": 5}, "Ендокринологічне": {"ОВД": 1}}.items():
        submit_order(open_order(w.depts[name], DAY), w.nurse[name], {diet(k).pk: v for k, v in portions.items()})
    for t in Task.objects.filter(status=TaskStatus.SUBMITTED):
        wf.approve(t, w.dietician)
    c = correct(w, "Хірургічне", {"ЩД": 2}, from_meal="breakfast")
    assert c.applied_at is None                                       # nothing frozen yet to add to
    plan = open_plan(DAY)
    wf.submit(Task.objects.get(type=MENU_PLANNING, service_date=DAY), w.dietician)
    wf.approve(Task.objects.get(type=MENU_PLANNING, service_date=DAY), w.chef)
    c.refresh_from_db()
    assert c.applied_at is not None and extra_brutto(c) > 0
    assert {e.diet.code: e.portions for e in plan.entries.all()}["ЩД"] == 5   # the plan itself: orders only


@pytest.mark.parametrize("deltas, message", [
    ({"ОВД": -31}, "у відділенні 30 порц. — не можна зняти 31"),
    ({}, "хоча б для однієї дієти"),
])
def test_impossible_corrections_are_refused(cooking_day, deltas, message):
    w = cooking_day
    c = corrections.open_correction(w.depts["Терапевтичне"], DAY, w.nurse["Терапевтичне"])
    with pytest.raises(ValidationError, match=message):
        corrections.save_correction(c, w.nurse["Терапевтичне"], {diet(k).pk: v for k, v in deltas.items()},
                                    from_meal="lunch", reason="discharge")


def test_a_diet_outside_the_plan_needs_some_menu(cooking_day):
    """НКД is not cooked that day: allowed from its cycle menu — unless it has no menu at all."""
    from apps.menus.models import Menu

    w = cooking_day
    c = corrections.open_correction(w.depts["Терапевтичне"], DAY, w.nurse["Терапевтичне"])
    corrections.save_correction(c, w.nurse["Терапевтичне"], {diet("НКД").pk: 1}, from_meal="lunch", reason="admission")
    Menu.objects.filter(diet=diet("НКД")).update(is_active=False)
    with pytest.raises(ValidationError, match="НКД немає жодного меню"):
        corrections.save_correction(c, w.nurse["Терапевтичне"], {diet("НКД").pk: 1}, from_meal="lunch",
                                    reason="admission")


def test_transfer_cannot_take_more_than_the_sender_has(cooking_day):
    w = cooking_day
    c = corrections.open_correction(w.depts["Хірургічне"], DAY, w.nurse["Хірургічне"])
    with pytest.raises(ValidationError, match="«Ендокринологічне» лише 2"):
        corrections.save_correction(c, w.nurse["Хірургічне"], {diet("ОВД").pk: 3}, from_meal="lunch",
                                    reason="transfer", transfer_from=w.depts["Ендокринологічне"])


def test_no_correction_before_the_order_is_approved_or_after_the_kitchen_fact(world, write_off_day):
    w = write_off_day
    with pytest.raises(ValidationError, match="Факт кухні"):
        corrections.open_correction(w.depts["Терапевтичне"], DAY, w.nurse["Терапевтичне"])
    other_day = date(2026, 10, 2)
    open_order(w.depts["Терапевтичне"], other_day)
    with pytest.raises(ValidationError, match="ще не затверджене"):
        corrections.open_correction(w.depts["Терапевтичне"], other_day, w.nurse["Терапевтичне"])


@pytest.mark.parametrize("hour, first", [(10, "lunch"), (15, "dinner")])
def test_today_lunch_until_14_then_dinner_only(world, hour, first):
    w = world
    today = date(2026, 9, 29)
    submit_order(open_order(w.depts["Терапевтичне"], today), w.nurse["Терапевтичне"], {diet("ОВД").pk: 5})
    wf.approve(Task.objects.get(service_date=today, department=w.depts["Терапевтичне"]), w.dietician)
    with time_machine.travel(datetime(2026, 9, 29, hour, 0, tzinfo=ZoneInfo("Europe/Kyiv")), tick=False):
        meals, why = corrections.allowed_meals(w.depts["Терапевтичне"], today)
    assert why is None and meals[0] == first and "breakfast" not in meals


def test_several_corrections_a_day_and_other_departments_cannot_touch_them(client, cooking_day):
    w = cooking_day
    correct(w, "Терапевтичне", {"ОВД": 1})
    second = correct(w, "Терапевтичне", {"ОВД": -1}, reason="discharge", approve=False)
    assert Task.objects.filter(type=ORDER_CORRECTION, department=w.depts["Терапевтичне"]).count() == 2
    assert as_user(client, w.nurse["Ендокринологічне"]).get(f"/orders/corrections/{second.pk}/").status_code == 404


def test_the_nurse_screens(client, cooking_day):
    w = cooking_day
    nurse = as_user(client, w.nurse["Терапевтичне"])
    page = nurse.get(f"/orders/{w.depts['Терапевтичне'].pk}/{DAY.isoformat()}/").content.decode()
    assert "Нове коригування" in page
    r = nurse.post(f"/orders/{w.depts['Терапевтичне'].pk}/{DAY.isoformat()}/corrections/new/")
    c = OrderCorrection.objects.get()
    assert r.url == f"/orders/corrections/{c.pk}/"
    r = nurse.post(r.url, {"action": "submit", "reason": "admission", "from_meal": "dinner",
                           f"diet_{diet('ОВД').pk}": "+2"}, follow=True)
    assert "Коригування подано дієтсестрі" in r.content.decode()
    c.refresh_from_db()
    assert c.task.status == TaskStatus.SUBMITTED and c.from_meal == "dinner"
    summary = as_user(client, w.dietician).get(f"/orders/summary/?date={DAY.isoformat()}").content.decode()
    assert "Коригування на 01.10" in summary and f"/tasks/{c.task.pk}/approve/" in summary


def test_the_admin_sees_corrections_but_does_not_submit_them(client, cooking_day):
    from django.core.exceptions import PermissionDenied

    w = cooking_day
    correct(w, "Терапевтичне", {"ОВД": 1})
    page = as_user(client, w.admin).get(f"/orders/{w.depts['Терапевтичне'].pk}/{DAY.isoformat()}/").content.decode()
    assert "Нове коригування" not in page and "Надходження" in page         # the list is there, the button is not
    assert "зміни лише коригуванням нижче" in page
    with pytest.raises(PermissionDenied):
        corrections.open_correction(w.depts["Терапевтичне"], DAY, w.admin)
    r = as_user(client, w.admin).post(f"/orders/{w.depts['Терапевтичне'].pk}/{DAY.isoformat()}/corrections/new/", follow=True)
    assert OrderCorrection.objects.count() == 1 and "старша медсестра" in r.content.decode()


def test_day_status_lists_tomorrows_corrections(client, cooking_day):
    from datetime import timedelta

    w = cooking_day
    correct(w, "Хірургічне", {"ЩД": 2}, approve=False)
    r = as_user(client, w.admin).get(f"/tasks/day/?date={(DAY - timedelta(days=1)).isoformat()}")
    tomorrow = r.context["cards"][0]
    assert any(row["what"] == "Коригування · Хірургічне" and row["status"] == "submitted" for row in tomorrow["rows"])
