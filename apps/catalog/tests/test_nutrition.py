from decimal import Decimal

import pytest

from apps.catalog.models import Dish
from apps.catalog.nutrition import annotate_dish_nutrition, dish_nutrition, norm_status
from apps.catalog.tests.factories import DietFactory, DishFactory, IngredientFactory, ProductFactory

pytestmark = pytest.mark.django_db


def test_dish_nutrition_python_and_sql_agree():
    dish = DishFactory()
    IngredientFactory(dish=dish, netto_g=Decimal("50"),
                      product=ProductFactory(protein=Decimal("12"), fat=Decimal("6"), carbs=Decimal("60"), kcal=Decimal("350")))
    IngredientFactory(dish=dish, brutto_g=Decimal("150"), netto_g=Decimal("150"),
                      product=ProductFactory(protein=Decimal("3"), fat=Decimal("2.5"), carbs=Decimal("4.7"), kcal=Decimal("52")))
    py = dish_nutrition(dish).rounded()
    sql = annotate_dish_nutrition(Dish.objects.filter(pk=dish.pk)).get()
    assert py.kcal == Decimal("253.0")
    assert (py.protein, py.fat, py.carbs) == (Decimal("10.5"), Decimal("6.8"), Decimal("37.1"))
    assert sql.n_kcal.quantize(Decimal("0.1")) == py.kcal


def test_dish_without_ingredients_is_zero():
    assert annotate_dish_nutrition(Dish.objects.filter(pk=DishFactory().pk)).get().n_kcal == 0


def test_diet_filter_does_not_multiply_sums():
    a, b = DietFactory(code="A"), DietFactory(code="B")
    dish = DishFactory(diets=[a, b])
    IngredientFactory(dish=dish, netto_g=Decimal("100"), product=ProductFactory(kcal=Decimal("100")))
    qs = annotate_dish_nutrition(Dish.objects.filter(diets__in=[a, b]).distinct())
    assert {d.n_kcal for d in qs} == {Decimal("100")}


@pytest.mark.parametrize(
    "value,expected",
    [(2200, "ok"), (2170, "ok"), (2000, "warn"), (1960, "warn"), (1900, "bad"),
     (2500, "warn"), (2700, "bad")],
)
def test_norm_status(value, expected):
    assert norm_status(Decimal(value), 2170, 2400) == expected


def test_norm_status_without_norm():
    assert norm_status(Decimal("100"), None, None) is None
