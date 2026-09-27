from decimal import Decimal

import factory

from apps.catalog.models import Diet, Dish, DishIngredient, Product
from apps.menus.models import Menu, MenuItem


class DietFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Diet
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"D{n}")
    name = factory.LazyAttribute(lambda o: f"Дієта {o.code}")
    kind = Diet.Kind.VARIANT
    kcal_min = 2000
    kcal_max = 2400


class ProductFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Product
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"Продукт {n}")
    protein = Decimal("10")
    fat = Decimal("5")
    carbs = Decimal("50")
    kcal = Decimal("300")


class DishFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Dish
        skip_postgeneration_save = True

    card_no = factory.Sequence(lambda n: 1000 + n)
    name = factory.Sequence(lambda n: f"Страва {n}")
    category = "Обід — основні"
    output_g = Decimal("200")

    @factory.post_generation
    def diets(self, create, extracted, **kwargs):
        if create and extracted:
            self.diets.set(extracted)


class IngredientFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DishIngredient

    dish = factory.SubFactory(DishFactory)
    product = factory.SubFactory(ProductFactory)
    brutto_g = Decimal("100")
    netto_g = Decimal("80")


class MenuFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Menu

    number = factory.Sequence(lambda n: 5000 + n)
    diet = factory.SubFactory(DietFactory)


class MenuItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MenuItem

    menu = factory.SubFactory(MenuFactory)
    meal_slot = "lunch"
    dish = factory.SubFactory(DishFactory)
