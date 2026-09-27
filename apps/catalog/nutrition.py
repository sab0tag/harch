"""Nutrition math for dishes and menus. Deterministic, no AI (CLAUDE.md, «ИИ»).

Values per portion are Σ(netto_g × per-100g value / 100) over ingredients.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import DecimalField, F, Sum

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


def _grouped_sums(queryset, group_field, prefix):
    """{group_id: Nutrition} in ONE grouped query (joins + GROUP BY, no correlated subqueries).

    Correlated subqueries per row degrade badly when table statistics are stale (fresh seed
    load): a single menu-list query took minutes. A grouped join stays fast either way.
    """
    sums = {
        f: Sum(F(f"{prefix}netto_g") * F(f"{prefix}product__{f}") / 100, output_field=_DEC)
        for f in FIELDS
    }
    rows = queryset.order_by().values(group_field).annotate(**sums)
    return {r[group_field]: Nutrition(*(r[f] or ZERO for f in FIELDS)) for r in rows}


def _attach(objects, sums):
    for obj in objects:
        n = sums.get(obj.pk, Nutrition())
        obj.n_protein, obj.n_fat, obj.n_carbs, obj.n_kcal = n.protein, n.fat, n.carbs, n.kcal
    return objects


def with_dish_nutrition(dishes):
    """Sets n_protein/n_fat/n_carbs/n_kcal (per portion) on each dish; returns the list."""
    dishes = list(dishes)
    sums = _grouped_sums(DishIngredient.objects.filter(dish__in=[d.pk for d in dishes]), "dish_id", "")
    return _attach(dishes, sums)


def with_menu_nutrition(menus):
    """Sets n_* per person per day on each menu. A dish listed twice in a menu counts twice."""
    from apps.menus.models import MenuItem

    menus = list(menus)
    sums = _grouped_sums(
        MenuItem.objects.filter(menu__in=[m.pk for m in menus]), "menu_id", "dish__ingredients__"
    )
    return _attach(menus, sums)


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
