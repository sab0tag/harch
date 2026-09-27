from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.catalog.models import MealSlot
from apps.catalog.tests.factories import DietFactory, DishFactory, IngredientFactory, MenuItemFactory, ProductFactory
from apps.orgstructure.models import Department

pytestmark = pytest.mark.django_db


def test_netto_greater_than_brutto_is_rejected_by_db():
    with pytest.raises(IntegrityError), transaction.atomic():
        IngredientFactory(brutto_g=Decimal("50"), netto_g=Decimal("60"))


def test_same_product_twice_in_dish_is_rejected():
    ing = IngredientFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        IngredientFactory(dish=ing.dish, product=ing.product)


def test_waste_pct():
    assert IngredientFactory(brutto_g=Decimal("100"), netto_g=Decimal("75")).waste_pct == Decimal("25")


def test_diet_defaults_to_all_meal_slots():
    assert DietFactory().meal_slots == list(MealSlot.values)


def test_menu_item_rejects_dish_not_allowed_for_menu_diet():
    ovd, nkd = DietFactory(code="ОВД"), DietFactory(code="НКД")
    item = MenuItemFactory.build(menu=MenuItemFactory(menu__diet=ovd).menu, dish=DishFactory(diets=[nkd]))
    with pytest.raises(ValidationError):
        item.full_clean()


def test_menu_item_accepts_allowed_dish():
    ovd = DietFactory(code="ОВД")
    item = MenuItemFactory(menu__diet=ovd, dish=DishFactory(diets=[ovd]))
    item.full_clean()


def test_dish_edit_is_versioned():
    dish = DishFactory(name="Було")
    dish.name = "Стало"
    dish.save()
    assert [h.name for h in dish.history.all()] == ["Стало", "Було"]


def test_department_default_diet():
    dept = Department.objects.create(name="Хірургічне", default_diet=DietFactory(code="ЩД"))
    assert dept.default_diet.code == "ЩД"


def test_product_price_optional():
    assert ProductFactory().price_per_kg is None
