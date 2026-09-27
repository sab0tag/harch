"""Nutrition math for dishes and menus. Deterministic, no AI (CLAUDE.md, «ИИ»).

Values per portion are Σ(netto_g × per-100g value / 100) over ingredients.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Subquery, Sum
from django.db.models.functions import Coalesce

from .models import DishIngredient

FIELDS = ("protein", "fat", "carbs", "kcal")
_DEC = DecimalField(max_digits=14, decimal_places=4)
ZERO = Decimal("0")


@dataclass(frozen=True)
class Nutrition:
    protein: Decimal = ZERO
    fat: Decimal = ZERO
    carbs: Decimal = ZERO
    kcal: Decimal = ZERO

    def __add__(self, other):
        return Nutrition(*(getattr(self, f) + getattr(other, f) for f in FIELDS))

    def rounded(self, places="0.1"):
        q = Decimal(places)
        return Nutrition(*(getattr(self, f).quantize(q, ROUND_HALF_UP) for f in FIELDS))


def ingredient_nutrition(ingredient) -> Nutrition:
    p = ingredient.product
    k = ingredient.netto_g / 100
    return Nutrition(p.protein * k, p.fat * k, p.carbs * k, p.kcal * k)


def dish_nutrition(dish) -> Nutrition:
    """Per portion. Uses prefetched `ingredients__product` when available."""
    total = Nutrition()
    for ing in dish.ingredients.all():
        total += ingredient_nutrition(ing)
    return total


def _sum_expr(field, prefix=""):
    return ExpressionWrapper(Sum(F(f"{prefix}netto_g") * F(f"{prefix}product__{field}") / 100), output_field=_DEC)


def annotate_dish_nutrition(queryset):
    """Adds n_protein, n_fat, n_carbs, n_kcal (per portion) via subqueries — safe with M2M filters."""
    annotations = {}
    for field in FIELDS:
        sub = (
            DishIngredient.objects.filter(dish=OuterRef("pk"))
            .order_by()
            .values("dish")
            .annotate(v=_sum_expr(field))
            .values("v")
        )
        annotations[f"n_{field}"] = Coalesce(Subquery(sub, output_field=_DEC), ZERO, output_field=_DEC)
    return queryset.annotate(**annotations)


def annotate_menu_nutrition(queryset):
    """Adds n_* per person per day. A dish listed twice in a menu counts twice."""
    annotations = {}
    for field in FIELDS:
        sub = (
            DishIngredient.objects.filter(dish__menu_items__menu=OuterRef("pk"))
            .order_by()
            .values("dish__menu_items__menu")
            .annotate(v=_sum_expr(field))
            .values("v")
        )
        annotations[f"n_{field}"] = Coalesce(Subquery(sub, output_field=_DEC), ZERO, output_field=_DEC)
    return queryset.annotate(**annotations)


def norm_status(value, low, high, tolerance=Decimal("0.10")):
    """'ok' inside [low, high]; 'warn' within ±10% outside; 'bad' beyond; None if no norm."""
    if value is None or (low is None and high is None):
        return None
    value = Decimal(value)
    if (low is None or value >= low) and (high is None or value <= high):
        return "ok"
    if low is not None and value < low and value >= Decimal(low) * (1 - tolerance):
        return "warn"
    if high is not None and value > high and value <= Decimal(high) * (1 + tolerance):
        return "warn"
    return "bad"
