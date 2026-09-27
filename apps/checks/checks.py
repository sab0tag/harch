"""Deterministic reference-data checks. Each returns [Draft]; no model calls, no writes."""
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher

from django.urls import reverse

from apps.core.text import uk_sort_key

ENERGY_TOLERANCE = Decimal("0.10")     # docs/07_ai.md, AI-09: ккал ≠ 4·Б+9·Ж+4·В by more than 10%
SIMILAR_NAMES = 0.85


@dataclass
class Draft:
    check_code: str
    severity: str
    title: str
    detail: str = ""
    link: str = ""


def _listing(items, limit=15):
    items = list(items)
    more = f" … ще {len(items) - limit}" if len(items) > limit else ""
    return ", ".join(items[:limit]) + more


def menus_vs_norm():
    """Per diet: menus whose daily kcal is outside the diet norm (warn = within 10%, bad = beyond)."""
    from apps.catalog.models import Diet
    from apps.catalog.nutrition import norm_status, with_menu_nutrition
    from apps.menus.models import Menu

    drafts = []
    for diet in Diet.objects.filter(has_own_menus=True, is_active=True).order_by("sort_order", "code"):
        low, high = diet.norm("kcal")
        if low is None and high is None:
            continue
        menus = with_menu_nutrition(Menu.objects.filter(diet=diet, is_active=True).order_by("number"))
        if not menus:
            continue
        status = {m.pk: norm_status(m.n_kcal, low, high) for m in menus}
        off = [m for m in menus if status[m.pk] in ("warn", "bad")]
        if not off:
            continue
        below = [m for m in off if low is not None and m.n_kcal < low]
        avg = sum(Decimal(m.n_kcal or 0) for m in menus) / len(menus)
        norm = f"{low or '…'}–{high or '…'}"
        drafts.append(Draft(
            "menus_vs_norm",
            "bad" if any(status[m.pk] == "bad" for m in off) else "warn",
            f"Дієта {diet.code}: {len(off)} з {len(menus)} меню поза нормою ккал ({norm})",
            f"Нижче норми: {len(below)}, вище: {len(off) - len(below)}. Середнє по дієті: {avg:.0f} ккал. "
            f"Меню: {_listing(f'№{m.number} ({m.n_kcal:.0f})' for m in off)}.",
            reverse("menus:list") + f"?diet={diet.code}&status=bad",
        ))
    return drafts


def product_energy():
    """Declared kcal/100 g vs 4·protein + 9·fat + 4·carbs."""
    from apps.catalog.models import Product

    bad = []
    for p in Product.objects.filter(is_active=True):
        computed = 4 * p.protein + 9 * p.fat + 4 * p.carbs
        if computed <= 0 or p.kcal is None:
            continue
        if abs(p.kcal - computed) > computed * ENERGY_TOLERANCE:
            bad.append((p.name, p.kcal, computed))
    if not bad:
        return []
    bad.sort(key=lambda r: uk_sort_key(r[0]))
    return [Draft(
        "product_energy", "warn",
        f"{len(bad)} продукт(ів): ккал не сходиться з 4·Б + 9·Ж + 4·В (> 10%)",
        _listing(f"{n}: {k:.0f} замість ≈{c:.0f}" for n, k, c in bad) + ".",
        reverse("catalog:product_list"),
    )]


def similar_products():
    """Likely duplicates: product names that differ by a letter or two."""
    from apps.catalog.models import Product

    names = sorted(Product.objects.filter(is_active=True).values_list("name", flat=True), key=uk_sort_key)
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if SequenceMatcher(None, a.lower(), b.lower()).ratio() >= SIMILAR_NAMES:
                pairs.append(f"«{a}» / «{b}»")
    if not pairs:
        return []
    return [Draft("similar_products", "warn", f"{len(pairs)} пар(и) продуктів зі схожими назвами — можливі дублі",
                  _listing(pairs, 10) + ".", reverse("catalog:product_list"))]


def products_without_price():
    from apps.catalog.models import Product

    names = sorted(Product.objects.filter(is_active=True, price_per_kg__isnull=True).values_list("name", flat=True), key=uk_sort_key)
    if not names:
        return []
    return [Draft("products_without_price", "warn",
                  f"{len(names)} продукт(ів) без ціни — списання з ними не подати",
                  _listing(names) + ".", reverse("catalog:product_list"))]


def dishes_without_technology():
    from apps.catalog.models import Dish

    dishes = list(Dish.objects.filter(is_active=True, technology="").order_by("card_no").values_list("card_no", "name"))
    if not dishes:
        return []
    return [Draft("dishes_without_technology", "info", f"{len(dishes)} карток без технології приготування",
                  _listing(f"№{n} {name}" for n, name in dishes) + ".", reverse("catalog:dish_list"))]


def unverified():
    from apps.catalog.models import Dish, Product
    from apps.menus.models import Menu

    counts = {label: model.objects.filter(is_active=True, verified=False).count()
              for label, model in (("карток", Dish), ("меню", Menu), ("продуктів", Product))}
    if not any(counts.values()):
        return []
    return [Draft("unverified", "info",
                  "Не перевірено дієтсестрою: " + ", ".join(f"{v} {k}" for k, v in counts.items() if v),
                  "Стартові дані з Excel-прототипу. Перед боєвим використанням — перевірити й підтвердити.",
                  reverse("catalog:dish_list") + "?state=unverified")]


ALL_CHECKS = [menus_vs_norm, product_energy, similar_products, products_without_price,
              dishes_without_technology, unverified]


def run_all():
    return [draft for check in ALL_CHECKS for draft in check()]
