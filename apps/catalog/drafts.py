"""Turning accepted AI drafts into catalog records (AI-06, AI-07). Everything created here is
verified=False; numbers are re-validated in code, whatever the draft says."""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import Diet, Dish, DishIngredient, Origin, Product, ProductCategory
from .nutrition import Nutrition

D = Decimal


def to_dec(raw, label, errors, *, allow_empty=False, low=D(0), high=D(5000)):
    raw = ("" if raw is None else str(raw)).strip().replace(",", ".")
    if raw == "":
        if not allow_empty:
            errors.append(f"{label}: вкажіть значення.")
        return None
    try:
        value = D(raw)
    except InvalidOperation:
        errors.append(f"{label}: має бути число.")
        return None
    if not value.is_finite() or value < low or value > high:
        errors.append(f"{label}: значення поза межами {low}–{high}.")
        return None
    return value.quantize(D("0.01"))


def parse_rows(post, count):
    """Ingredient rows from the draft form: product_i, brutto_i, netto_i. Empty rows are skipped."""
    rows, errors, seen = [], [], set()
    products = {p.pk: p for p in Product.objects.filter(is_active=True)}
    for i in range(count):
        pid = post.get(f"product_{i}", "")
        if not pid:
            continue
        product = products.get(int(pid)) if pid.isdigit() else None
        if product is None:
            errors.append(f"Рядок {i + 1}: невідомий продукт.")
            continue
        brutto = to_dec(post.get(f"brutto_{i}"), f"{product.name}, брутто", errors, low=D("0.01"))
        netto = to_dec(post.get(f"netto_{i}"), f"{product.name}, нетто", errors)
        if brutto is not None and netto is not None and netto > brutto:
            errors.append(f"{product.name}: нетто не може бути більше за брутто.")
        if product.pk in seen:
            errors.append(f"{product.name}: продукт уже є в картці.")
        seen.add(product.pk)
        rows.append({"product": product, "brutto_g": brutto, "netto_g": netto})
    if not rows:
        errors.append("Додайте хоча б один продукт.")
    return rows, errors


def rows_nutrition(rows):
    total = Nutrition()
    for r in rows:
        if r["netto_g"] is None:
            continue
        k = r["netto_g"] / 100
        p = r["product"]
        total += Nutrition(p.protein * k, p.fat * k, p.carbs * k, p.kcal * k)
    return total.rounded()


def next_card_no():
    return (Dish.objects.aggregate(m=Max("card_no"))["m"] or 0) + 1


@transaction.atomic
def create_dish(*, card_no, name, category, output_g, diets, technology, rows, draft=None):
    dish = Dish.objects.create(
        card_no=card_no, name=name, category=category, output_g=output_g, technology=technology,
        source=f"Чернетка ШІ від {timezone.localdate():%d.%m.%Y} — перевірити норми", origin=Origin.AI_DRAFT,
        verified=False,
    )
    dish.diets.set(Diet.objects.filter(pk__in=diets))
    for i, r in enumerate(rows):
        DishIngredient.objects.create(dish=dish, product=r["product"], brutto_g=r["brutto_g"],
                                      netto_g=r["netto_g"], sort_order=i)
    if draft is not None:
        draft.status, draft.created_ids = draft.Status.ACCEPTED, [dish.pk]
        draft.save(update_fields=["status", "created_ids", "updated_at"])
    return dish


@transaction.atomic
def create_products(rows, *, model_name, draft=None):
    """rows: dicts with name, category, protein, fat, carbs, kcal (already validated)."""
    created = []
    stamp = f"Оцінка ШІ ({model_name}), {timezone.localdate():%d.%m.%Y} — звірити з довідником хімічного складу"
    for r in rows:
        category = ProductCategory.objects.filter(name=r["category"]).first() if r.get("category") else None
        created.append(Product.objects.create(
            name=r["name"], category=category, protein=r["protein"], fat=r["fat"], carbs=r["carbs"],
            kcal=r["kcal"], origin=Origin.AI_ESTIMATE, source=stamp[:200], verified=False,
        ))
    if draft is not None:
        draft.status, draft.created_ids = draft.Status.ACCEPTED, [p.pk for p in created]
        draft.save(update_fields=["status", "created_ids", "updated_at"])
    return created
