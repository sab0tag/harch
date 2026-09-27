"""S1-05: control example from docs/02_data_model.md — numbers must match exactly."""
from decimal import Decimal

import pytest

from apps.catalog.nutrition import with_menu_nutrition
from apps.catalog.tests.factories import DietFactory, DishFactory, IngredientFactory, MenuFactory, MenuItemFactory, ProductFactory
from apps.consumption.calc import CalcError, PlanInput, compute_lines, totals_by_product

pytestmark = pytest.mark.django_db

CONTROL = [PlanInput(menu_number=1, portions=10), PlanInput(menu_number=81, portions=5)]


@pytest.fixture
def control_totals(seeded):
    return {t.product_name: t for t in totals_by_product(compute_lines(CONTROL))}


@pytest.mark.parametrize(
    "product,brutto",
    [("Молоко 2.5%", "2850"), ("Масло вершкове", "235"), ("Цукор", "675"),
     ("Картопля", "5700"), ("Хліб білий", "1900")],
)
def test_control_example_brutto(control_totals, product, brutto):
    assert control_totals[product].brutto_g == Decimal(brutto)


def test_control_example_product_count_and_grand_total(control_totals):
    assert len(control_totals) == 30
    assert sum(t.brutto_g for t in control_totals.values()) == Decimal("40835")


def test_control_example_kcal_per_person(seeded):
    from apps.menus.models import Menu as M

    kcal = {m.number: m.n_kcal.quantize(Decimal("0.1")) for m in with_menu_nutrition(M.objects.filter(number__in=[1, 81]))}
    assert kcal == {1: Decimal("1980.8"), 81: Decimal("2034.9")}


def test_dish_listed_twice_is_counted_twice(db):
    diet = DietFactory(code="ОВД")
    bread = ProductFactory(name="Хліб")
    dish = DishFactory(diets=[diet])
    IngredientFactory(dish=dish, product=bread, brutto_g=Decimal("50"), netto_g=Decimal("50"))
    menu = MenuFactory(number=1, diet=diet)
    MenuItemFactory(menu=menu, dish=dish, meal_slot="breakfast")
    MenuItemFactory(menu=menu, dish=dish, meal_slot="lunch")
    [total] = totals_by_product(compute_lines([PlanInput(1, 3)]))
    assert total.brutto_g == Decimal("300")


def test_brutto_and_netto_are_separate(db):
    diet = DietFactory(code="ОВД")
    dish = DishFactory(diets=[diet])
    IngredientFactory(dish=dish, brutto_g=Decimal("100"), netto_g=Decimal("75"))
    MenuItemFactory(menu=MenuFactory(number=1, diet=diet), dish=dish)
    [t] = totals_by_product(compute_lines([PlanInput(1, 4)]))
    assert (t.brutto_g, t.netto_g) == (Decimal("400"), Decimal("300"))


def test_zero_portions_produce_no_lines(seeded):
    assert compute_lines([PlanInput(1, 0)]) == []


def test_unknown_menu_is_an_error(db):
    with pytest.raises(CalcError):
        compute_lines([PlanInput(999, 1)])


def test_two_menus_for_same_diet_is_an_error(seeded):
    with pytest.raises(CalcError):
        compute_lines([PlanInput(1, 5), PlanInput(2, 5)])  # both ОВД


def test_negative_portions_is_an_error(seeded):
    with pytest.raises(CalcError):
        compute_lines([PlanInput(1, -1)])
