"""Behavioral snapshot audit: reference edits must not rewrite an approved day."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.accounts.models import Role
from apps.accounts.tests.factories import DepartmentFactory, UserFactory
from apps.catalog.tests.factories import (
    DietFactory, DishFactory, IngredientFactory, MenuFactory, MenuItemFactory, ProductFactory,
)
from apps.consumption import services as consumption
from apps.consumption.calc import PlanInput, compute_lines
from apps.core.testing import on_the_day
from apps.orders import services as orders
from apps.planning.models import DailyPlan
from apps.reports.exports import accumulative_data, menu_vymoha_data
from apps.reports.pdf import menu_rozkladka_context
from apps.workflow import services as workflow
from apps.workflow.definitions import COOKING, WRITE_OFF
from apps.workflow.models import Notification, Task, TaskEvent

pytestmark = pytest.mark.django_db
DAY = date(2026, 10, 1)


@pytest.fixture
def frozen_day():
    """One department, one menu, one ingredient; real order → plan approval."""
    diet = DietFactory(code="AUDIT", name="Початкова дієта")
    product = ProductFactory(name="Початковий продукт", kcal=Decimal("300"),
                             price_per_kg=Decimal("20"))
    dish = DishFactory(name="Початкова страва", diets=[diet], output_g=Decimal("200"))
    ingredient = IngredientFactory(dish=dish, product=product,
                                   brutto_g=Decimal("100"), netto_g=Decimal("80"))
    menu = MenuFactory(diet=diet)
    item = MenuItemFactory(menu=menu, dish=dish, meal_slot="lunch")
    department = DepartmentFactory(default_diet=diet)
    nurse = UserFactory(role=Role.DEPT_NURSE, department=department)
    dietician = UserFactory(role=Role.DIETICIAN)
    chef = UserFactory(role=Role.CHEF)
    accountant = UserFactory(role=Role.ACCOUNTANT)
    order = orders.open_order(department, DAY, nurse)
    orders.submit_order(order, nurse, {diet.pk: 10})
    workflow.approve(order.task, dietician)
    plan = DailyPlan.objects.get(service_date=DAY)
    workflow.submit(plan.task, dietician)
    workflow.approve(plan.task, chef)
    plan.refresh_from_db()
    return SimpleNamespace(plan=plan, diet=diet, product=product, dish=dish,
                           ingredient=ingredient, menu=menu, item=item,
                           dietician=dietician, chef=chef, accountant=accountant)


def snapshot_values(plan):
    return list(plan.snapshot.order_by("pk").values())


def frozen_outputs(day):
    return {
        "totals": consumption.snapshot_totals(day.plan),
        "board": consumption.kitchen_board(day.plan),
        "card": consumption.scaled_card(day.plan, "lunch", day.dish.card_no),
        "menu": menu_rozkladka_context(day.plan)["diets"],
        "requisition": menu_vymoha_data(day.plan)["rows"],
    }


@pytest.mark.parametrize("mutation", [
    "brutto", "netto", "product_name", "product_kcal", "dish_name", "dish_output",
    "recipe_replacement", "menu_replacement",
])
def test_reference_edits_preserve_snapshot_and_historical_outputs(frozen_day, mutation):
    d = frozen_day
    before = snapshot_values(d.plan)
    output_before = frozen_outputs(d)
    live_before = compute_lines([PlanInput(d.menu.number, 10)])
    assert len(before) == 1
    assert (before[0]["brutto_total_g"], before[0]["netto_total_g"], before[0]["kcal_total"]) == (
        Decimal("1000"), Decimal("800"), Decimal("2400"),
    )
    if mutation in ("brutto", "netto"):
        setattr(d.ingredient, f"{mutation}_g", Decimal("150") if mutation == "brutto" else Decimal("50"))
        d.ingredient.save()
    elif mutation in ("product_name", "product_kcal"):
        if mutation == "product_name":
            d.product.name = "Новий продукт"
        else:
            d.product.kcal = Decimal("600")
        d.product.save()
    elif mutation in ("dish_name", "dish_output"):
        if mutation == "dish_name":
            d.dish.name = "Нова страва"
        else:
            d.dish.output_g = Decimal("450")
        d.dish.save()
    elif mutation == "recipe_replacement":
        d.ingredient.delete()
        IngredientFactory(dish=d.dish, brutto_g=Decimal("160"), netto_g=Decimal("120"))
    else:
        replacement = DishFactory(diets=[d.diet])
        IngredientFactory(dish=replacement)
        d.item.delete()
        MenuItemFactory(menu=d.menu, dish=replacement, meal_slot="dinner")
    # Prove that the edit actually changed a fresh calculation, avoiding vacuous coverage.
    assert compute_lines([PlanInput(d.menu.number, 10)]) != live_before
    assert snapshot_values(d.plan) == before
    assert frozen_outputs(d) == output_before


def test_catalog_price_is_live_for_consumption_but_not_part_of_snapshot(frozen_day):
    d = frozen_day
    before = snapshot_values(d.plan)
    initial = consumption.period_consumption(DAY, DAY)
    d.product.price_per_kg = Decimal("35")
    d.product.save()
    changed = consumption.period_consumption(DAY, DAY)
    assert snapshot_values(d.plan) == before
    assert changed["brutto_g"] == initial["brutto_g"] == Decimal("1000")
    assert (initial["cost"], changed["cost"]) == (Decimal("20"), Decimal("35"))


def workflow_values():
    return {model._meta.label: list(model.objects.order_by("pk").values())
            for model in (Task, TaskEvent, Notification, DailyPlan)}


def test_repeated_approval_and_freeze_do_not_rewrite_history(frozen_day):
    d = frozen_day
    before = snapshot_values(d.plan)
    workflow_before = workflow_values()
    with pytest.raises(workflow.InvalidTransition):
        workflow.approve(d.plan.task, d.chef)
    with pytest.raises(consumption.AlreadyFrozen):
        consumption.freeze(d.plan)
    assert snapshot_values(d.plan) == before
    assert workflow_values() == workflow_before
    assert Task.objects.filter(type=COOKING, service_date=DAY).count() == 1


def test_write_off_freezes_submission_prices_and_names_through_approval(frozen_day):
    d = frozen_day
    cooking = Task.objects.get(type=COOKING, service_date=DAY)
    consumption.ensure_actuals(d.plan)
    with on_the_day():
        workflow.submit(cooking, d.chef)
    workflow.approve(cooking, d.dietician)
    task = Task.objects.get(type=WRITE_OFF, service_date=DAY)
    wo = consumption.open_write_off(d.plan)
    # Prices are sampled at submission, not at the earlier menu approval.
    d.product.price_per_kg = Decimal("30")
    d.product.save()
    consumption.set_prices(wo, d.accountant, {d.product.pk: Decimal("40")})
    workflow.submit(task, d.accountant)
    before = list(wo.lines.order_by("pk").values())
    assert len(before) == 1
    assert (before[0]["price_per_kg"], before[0]["quantity_g"], before[0]["cost"]) == (
        Decimal("40"), Decimal("1000"), Decimal("40"),
    )
    d.product.price_per_kg = Decimal("99")
    d.product.name = "Перейменований продукт"
    d.product.save()
    wo.refresh_from_db()
    assert consumption.write_off_rows(wo)[0]["price_per_kg"] == Decimal("40")
    assert menu_vymoha_data(DailyPlan.objects.get(pk=d.plan.pk))["rows"][0]["price_per_kg"] == Decimal("40")
    workflow.approve(task, d.dietician)
    with pytest.raises(workflow.InvalidTransition):
        workflow.approve(task, d.dietician)
    assert list(wo.lines.order_by("pk").values()) == before
    wo.refresh_from_db()
    assert wo.total_cost == Decimal("40")
    accounting = accumulative_data(DAY.year, DAY.month)
    assert accounting["cost"] == Decimal("40")
    assert accounting["products"][0]["name"] == "Початковий продукт"


@pytest.mark.parametrize("field", ["code", "name"])
def test_historical_menu_report_preserves_diet_after_catalog_rename(frozen_day, field):
    d = frozen_day
    before = menu_rozkladka_context(d.plan)["diets"]
    snapshot_before = snapshot_values(d.plan)
    setattr(d.diet, field, "RENAMED")
    d.diet.save()
    assert snapshot_values(d.plan) == snapshot_before
    after = menu_rozkladka_context(DailyPlan.objects.get(pk=d.plan.pk))["diets"]
    assert after == before


def test_historical_menu_report_preserves_menu_number_after_renumbering(frozen_day):
    d = frozen_day
    before = menu_rozkladka_context(d.plan)["diets"]
    snapshot_before = snapshot_values(d.plan)
    d.menu.number += 100000
    d.menu.save()
    assert snapshot_values(d.plan) == snapshot_before
    assert menu_rozkladka_context(DailyPlan.objects.get(pk=d.plan.pk))["diets"] == before


def test_legacy_snapshot_without_diet_history_still_prints_frozen_dishes(frozen_day):
    from django.template.loader import render_to_string

    d = frozen_day
    before = menu_rozkladka_context(d.plan)["diets"]
    d.diet.history.all().delete()
    ctx = menu_rozkladka_context(d.plan)
    assert ctx["diets"] == [{**before[0], "name": ""}]
    html = render_to_string("reports/menu_rozkladka.html", ctx)
    assert "AUDIT</td>" in html
    assert "Початкова страва" in html
