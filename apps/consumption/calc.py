"""Consumption calculation — pure functions over menus and portions (CLAUDE.md invariant 4).

    розхід(продукт) = Σ_дієта порції(дієта) × Σ_страва_в_меню брутто(продукт у страві)

Brutto drives procurement and write-off; netto drives nutrition. A dish listed twice in a
menu (e.g. bread at breakfast and lunch) produces two lines and is summed.
"""
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from django.db.models import Prefetch

from apps.catalog.models import DishIngredient
from apps.menus.models import Menu, MenuItem


@dataclass(frozen=True)
class PlanInput:
    """One diet's choice for a day. DailyPlanEntry (Sprint 3) maps onto this."""

    menu_number: int
    portions: int


@dataclass(frozen=True)
class Line:
    diet_code: str
    menu_number: int
    meal_slot: str
    dish_card_no: int
    dish_name: str
    product_id: int
    product_name: str
    portions: int
    brutto_pp: Decimal
    netto_pp: Decimal
    kcal_per_100: Decimal
    dish_output_g: Decimal | None = None

    @property
    def brutto_total(self) -> Decimal:
        return self.brutto_pp * self.portions

    @property
    def netto_total(self) -> Decimal:
        return self.netto_pp * self.portions

    @property
    def kcal_total(self) -> Decimal:
        return self.netto_pp * self.kcal_per_100 / 100 * self.portions


@dataclass(frozen=True)
class ProductTotal:
    product_id: int
    product_name: str
    brutto_g: Decimal
    netto_g: Decimal


class CalcError(ValueError):
    pass


def _load_menus(numbers):
    ingredients = DishIngredient.objects.select_related("product").order_by("sort_order", "id")
    items = (
        MenuItem.objects.select_related("dish")
        .prefetch_related(Prefetch("dish__ingredients", queryset=ingredients))
        .order_by("sort_order", "id")
    )
    menus = Menu.objects.filter(number__in=numbers).select_related("diet").prefetch_related(
        Prefetch("items", queryset=items)
    )
    return {m.number: m for m in menus}


def compute_lines(entries: Iterable[PlanInput]) -> list[Line]:
    entries = [e for e in entries if e.portions]
    if any(e.portions < 0 for e in entries):
        raise CalcError("Кількість порцій не може бути від'ємною")
    menus = _load_menus({e.menu_number for e in entries})
    missing = {e.menu_number for e in entries} - set(menus)
    if missing:
        raise CalcError(f"Меню не знайдено: {sorted(missing)}")
    diets = [menus[e.menu_number].diet_id for e in entries]
    if len(diets) != len(set(diets)):
        raise CalcError("Для однієї дієти може бути лише одне меню на день")

    lines = []
    for entry in entries:
        menu = menus[entry.menu_number]
        for item in menu.items.all():
            for ing in item.dish.ingredients.all():
                lines.append(
                    Line(
                        diet_code=menu.diet.code,
                        menu_number=menu.number,
                        meal_slot=item.meal_slot,
                        dish_card_no=item.dish.card_no,
                        dish_name=item.dish.name,
                        product_id=ing.product_id,
                        product_name=ing.product.name,
                        portions=entry.portions,
                        brutto_pp=ing.brutto_g,
                        netto_pp=ing.netto_g,
                        kcal_per_100=ing.product.kcal,
                        dish_output_g=item.dish.output_g,
                    )
                )
    return lines


def totals_by_product(lines: Iterable[Line]) -> list[ProductTotal]:
    acc: "OrderedDict[int, list]" = OrderedDict()
    for line in lines:
        row = acc.setdefault(line.product_id, [line.product_name, Decimal("0"), Decimal("0")])
        row[1] += line.brutto_total
        row[2] += line.netto_total
    totals = [ProductTotal(pid, name, b, n) for pid, (name, b, n) in acc.items()]
    return sorted(totals, key=lambda t: t.product_name)
