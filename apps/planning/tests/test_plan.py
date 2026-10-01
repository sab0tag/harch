"""S3-03 / S3-04: day plan, submission to the chef, frozen consumption."""
from decimal import Decimal

import pytest

from apps.catalog.models import Diet, DishIngredient
from apps.consumption.calc import PlanInput, compute_lines, totals_by_product
from apps.consumption.models import ConsumptionSnapshotLine
from apps.menus.models import Menu
from apps.orders.services import open_order, submit_order
from apps.planning.models import DailyPlan
from apps.planning.services import open_plan, suggest_menu, sync_portions
from apps.planning.tests.conftest import DAY, as_user
from apps.workflow import services as wf
from apps.workflow.definitions import COOKING, MENU_PLANNING
from apps.workflow.models import Task, TaskStatus

pytestmark = pytest.mark.django_db
PLAN = "/plan/2026-10-01/"


def diet(code):
    return Diet.objects.get(code=code)


@pytest.fixture
def approved_day(world):
    """Orders: Терапевтичне ОВД 30; Хірургічне ЩД 20; Ендокринологічне №9 8 + ОВД 2 — all approved."""
    orders = {"Терапевтичне": {"ОВД": 30}, "Хірургічне": {"ЩД": 20}, "Ендокринологічне": {"№9": 8, "ОВД": 2}}
    for name, portions in orders.items():
        order = open_order(world.depts[name], DAY)
        submit_order(order, world.nurse[name], {diet(c).pk: v for c, v in portions.items()})
    for task in Task.objects.filter(status=TaskStatus.SUBMITTED):
        wf.approve(task, world.dietician)
    return world


def menus_form(plan, numbers):
    return {f"menu_{e.diet_id}": str(Menu.objects.get(number=numbers[e.diet.code]).pk) for e in plan.entries.all()}


def test_plan_created_with_portions_from_approved_orders(approved_day):
    plan = DailyPlan.objects.get(service_date=DAY)
    assert plan.task.type == MENU_PLANNING and plan.task.subject == plan
    assert {e.diet.code: e.portions for e in plan.entries.all()} == {"ОВД": 32, "ЩД": 20, "№9": 8}
    assert all(e.menu and e.menu.diet_id == e.diet_id for e in plan.entries.all())  # suggestions preselected


def test_suggestion_rotates_after_previous_day(approved_day):
    from datetime import timedelta

    ovd = diet("ОВД")
    prev = DailyPlan.objects.create(service_date=DAY - timedelta(days=1))
    prev.entries.create(diet=ovd, menu=Menu.objects.get(number=7), portions=1)
    assert suggest_menu(ovd, DAY).number == 8
    prev.entries.update(menu=Menu.objects.get(number=80))  # last ОВД menu → wraps to first
    assert suggest_menu(ovd, DAY).number == 1


def test_dietician_sets_menus_and_submits(client, approved_day):
    w = approved_day
    plan = DailyPlan.objects.get(service_date=DAY)
    client = as_user(client, w.dietician)
    assert "Подати шеф-кухарю" in client.get(PLAN).content.decode()
    data = {**menus_form(plan, {"ОВД": 1, "ЩД": 81, "№9": 221}), "action": "submit", "comment": "Без змін"}
    r = client.post(PLAN, data, follow=True)
    assert "подано шеф-кухарю" in r.content.decode()
    assert {e.diet.code: e.menu.number for e in plan.entries.all()} == {"ОВД": 1, "ЩД": 81, "№9": 221}
    assert Task.objects.get(type=MENU_PLANNING).status == TaskStatus.SUBMITTED


def test_menu_of_another_diet_is_rejected(client, approved_day):
    plan = DailyPlan.objects.get(service_date=DAY)
    ovd_entry = plan.entries.get(diet__code="ОВД")
    before = ovd_entry.menu_id
    r = as_user(client, approved_day.dietician).post(
        PLAN, {f"menu_{ovd_entry.diet_id}": str(Menu.objects.get(number=81).pk), "action": "save"}, follow=True)
    assert "не належить дієті ОВД" in r.content.decode()
    ovd_entry.refresh_from_db()
    assert ovd_entry.menu_id == before


def test_submit_without_menu_is_blocked(client, approved_day):
    plan = DailyPlan.objects.get(service_date=DAY)
    ovd = plan.entries.get(diet__code="ОВД")
    r = as_user(client, approved_day.dietician).post(PLAN, {f"menu_{ovd.diet_id}": "", "action": "submit"}, follow=True)
    assert "Оберіть меню для дієти ОВД" in r.content.decode()
    assert Task.objects.get(type=MENU_PLANNING).status != TaskStatus.SUBMITTED


def test_preview_shows_consumption_for_selected_menus_without_saving(client, approved_day):
    plan = DailyPlan.objects.get(service_date=DAY)
    before = {e.pk: e.menu_id for e in plan.entries.all()}
    query = menus_form(plan, {"ОВД": 1, "ЩД": 81, "№9": 221})
    r = as_user(client, approved_day.dietician).get(PLAN + "preview/", query)
    expected = totals_by_product(compute_lines([PlanInput(1, 32), PlanInput(81, 20), PlanInput(221, 8)]))
    assert [(n, b) for n, b, _ in r.context["consumption"]] == [(t.product_name, t.brutto_g) for t in expected]
    assert {e.pk: e.menu_id for e in plan.entries.all()} == before


class TestApproval:
    """S3-04 acceptance: 3 departments → plan → approve → snapshot == calc; later card edits don't leak."""

    @pytest.fixture
    def submitted(self, client, approved_day):
        plan = DailyPlan.objects.get(service_date=DAY)
        as_user(client, approved_day.dietician).post(
            PLAN, {**menus_form(plan, {"ОВД": 1, "ЩД": 81, "№9": 221}), "action": "submit"})
        return plan

    def approve(self, client, world):
        task = Task.objects.get(type=MENU_PLANNING)
        return as_user(client, world.chef).post(f"/tasks/{task.pk}/approve/", {"next": PLAN}, follow=True)

    def test_chef_sees_plan_and_approves(self, client, approved_day, submitted):
        html = as_user(client, approved_day.chef).get(PLAN).content.decode()
        assert "Затвердити план" in html and "Подати шеф-кухарю" not in html
        self.approve(client, approved_day)
        submitted.refresh_from_db()
        assert submitted.is_frozen
        assert Task.objects.filter(type=COOKING, service_date=DAY).count() == 1

    def test_snapshot_equals_calc(self, client, approved_day, submitted):
        self.approve(client, approved_day)
        expected = {t.product_name: t.brutto_g for t in totals_by_product(
            compute_lines([PlanInput(1, 32), PlanInput(81, 20), PlanInput(221, 8)]))}
        got = {}
        for row in ConsumptionSnapshotLine.objects.filter(plan=submitted):
            got[row.product_name] = got.get(row.product_name, Decimal(0)) + row.brutto_total_g
        assert got == expected

    def test_card_edit_after_approval_does_not_change_snapshot_or_plan_page(self, client, approved_day, submitted):
        self.approve(client, approved_day)
        before = list(ConsumptionSnapshotLine.objects.filter(plan=submitted).values_list("product_name", "brutto_total_g"))
        page_before = as_user(client, approved_day.chef).get(PLAN).context["consumption"]
        DishIngredient.objects.filter(dish__card_no=1).update(brutto_g=Decimal("999"), netto_g=Decimal("999"))
        after = list(ConsumptionSnapshotLine.objects.filter(plan=submitted).values_list("product_name", "brutto_total_g"))
        assert after == before
        assert as_user(client, approved_day.chef).get(PLAN).context["consumption"] == page_before

    def test_approved_plan_is_locked(self, client, approved_day, submitted):
        self.approve(client, approved_day)
        entry = submitted.entries.get(diet__code="ОВД")
        r = as_user(client, approved_day.dietician).post(
            PLAN, {f"menu_{entry.diet_id}": str(Menu.objects.get(number=2).pk), "action": "save"}, follow=True)
        assert "не можна змінювати" in r.content.decode()
        entry.refresh_from_db()
        assert entry.menu.number == 1

    def test_chef_cannot_edit_menus(self, client, approved_day, submitted):
        entry = submitted.entries.get(diet__code="ОВД")
        as_user(client, approved_day.chef).post(PLAN, {f"menu_{entry.diet_id}": str(Menu.objects.get(number=2).pk)})
        entry.refresh_from_db()
        assert entry.menu.number == 1


def test_portions_follow_orders_only_while_plan_is_with_dietician(client, world):
    """Forced early plan: later approvals update it until it is submitted; after that a late order goes in
    as «Пізнє замовлення», a diet the plan lacks from its cycle menu."""
    for name, code, n in (("Терапевтичне", "ОВД", 30), ("Хірургічне", "ЩД", 20), ("Ендокринологічне", "№9", 8)):
        submit_order(open_order(world.depts[name], DAY), world.nurse[name], {diet(code).pk: n})
    tasks = {t.department.name: t for t in Task.objects.filter(status=TaskStatus.SUBMITTED)}
    wf.approve(tasks["Терапевтичне"], world.dietician)
    from apps.planning.services import force_planning

    plan = force_planning(DAY, world.dietician)
    assert {e.diet.code: e.portions for e in plan.entries.all()} == {"ОВД": 30}

    wf.approve(tasks["Хірургічне"], world.dietician)
    as_user(client, world.dietician).get(PLAN)  # dietician opens the plan → portions refresh
    assert {e.diet.code: e.portions for e in plan.entries.all()} == {"ОВД": 30, "ЩД": 20}

    as_user(client, world.dietician).post(PLAN, {**menus_form(plan, {"ОВД": 1, "ЩД": 81}), "action": "submit"})
    # Late: the chef reviews exactly what was submitted; the order goes in as «Пізнє замовлення»
    # (№9, which the plan lacks, from its cycle menu).
    wf.approve(tasks["Ендокринологічне"], world.dietician)
    assert {e.diet.code: e.portions for e in plan.entries.all()} == {"ОВД": 30, "ЩД": 20}
    from apps.orders.models import OrderCorrection

    late = OrderCorrection.objects.get(reason=OrderCorrection.Reason.LATE_ORDER)
    assert {l.diet.code: l.delta for l in late.lines.all()} == {"№9": 8}


def test_access(client, approved_day):
    assert as_user(client, approved_day.accountant).get(PLAN).status_code == 200
    assert as_user(client, approved_day.nurse["Терапевтичне"]).get(PLAN).status_code == 403


def test_no_plan_yet_page(client, world):
    html = as_user(client, world.dietician).get("/plan/2026-10-05/").content.decode()
    assert "План ще не сформовано" in html


def test_plan_screen_opens_on_the_working_day_until_tomorrows_plan_starts(client, world):
    from datetime import date

    from apps.planning.services import default_plan_date
    from apps.workflow import services as wf2

    assert default_plan_date() == date(2026, 9, 29)                     # today: nothing planned for tomorrow yet
    wf2.ensure_task(MENU_PLANNING, date(2026, 9, 30))
    assert default_plan_date() == date(2026, 9, 30)
