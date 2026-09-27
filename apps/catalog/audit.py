"""AI-09: reference data audit. Every rule here is plain code; the model is only asked to group
product names that may mean the same thing (see apps/assist/features.py).

Run nightly by `audit_reference` and on demand from «Перевірка довідника». A run upserts findings:
new problems open, problems that disappeared are marked resolved, dismissed ones stay dismissed.
"""
import difflib
import re
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import Dish, Product, ReferenceFinding
from .nutrition import norm_status, with_dish_nutrition, with_menu_nutrition

KCAL_TOLERANCE = Decimal("0.10")      # |kcal − (4Б + 9Ж + 4В)| > 10% (and > 10 kcal)
OUTPUT_RANGE = (Decimal("0.5"), Decimal("1.6"))   # вихід / Σ нетто
FUZZY = 0.88

KINDS = {
    "product_kcal": "Ккал не відповідають БЖУ",
    "product_duplicate": "Можливі дублі продуктів",
    "product_duplicate_ai": "Можливі дублі продуктів (ШІ)",
    "product_ai_estimate": "Значення БЖУ — оцінка ШІ",
    "dish_no_ingredients": "Картка без складу",
    "dish_netto_gt_brutto": "Нетто більше брутто",
    "dish_output": "Вихід не узгоджується з нетто",
    "dish_no_technology": "Немає технології приготування",
    "menu_norm": "Меню поза нормою дієти",
    "menu_dish_not_allowed": "Страва не дозволена для дієти меню",
}


@dataclass
class Found:
    kind: str
    severity: str
    obj_type: str
    obj_id: int
    title: str
    message: str
    details: dict = field(default_factory=dict)
    source: str = "rule"


def normalize(name: str) -> str:
    name = name.lower().replace("ё", "е").replace("’", "'").replace("ʼ", "'")
    name = re.sub(r"(\d),(\d)", r"\1.\2", name)       # 2,5% = 2.5%
    name = re.sub(r"[^\w%.' ]+", " ", name)
    name = re.sub(r"\s*%", "%", name)                    # "2.5 %" = "2.5%"
    return re.sub(r"\s+", " ", name).strip()


# ------------------------------------------------------------------ rules
def check_products(products) -> list[Found]:
    out = []
    for p in products:
        atwater = 4 * p.protein + 9 * p.fat + 4 * p.carbs
        diff = abs(p.kcal - atwater)
        if diff > 10 and diff > KCAL_TOLERANCE * max(p.kcal, atwater):
            out.append(Found("product_kcal", "warn", "product", p.pk, p.name,
                             f"Ккал {p.kcal:g} на 100 г, а за БЖУ виходить {atwater:.0f} (4·Б + 9·Ж + 4·В).",
                             {"kcal": str(p.kcal), "atwater": f"{atwater:.1f}"}))
        if getattr(p, "origin", "") == "ai_estimate" and not p.verified:
            out.append(Found("product_ai_estimate", "info", "product", p.pk, p.name,
                             "БЖУ і ккал запропонував ШІ — звірте з довідником хімічного складу і позначте як перевірене."))
    out += check_duplicates(products)
    return out


def check_duplicates(products) -> list[Found]:
    """Same normalised name, or very similar names (difflib). Obvious variants only."""
    out, seen = [], set()
    items = [(p, normalize(p.name)) for p in products]
    for i, (a, na) in enumerate(items):
        for b, nb in items[i + 1:]:
            if (a.pk, b.pk) in seen:
                continue
            ratio = 1.0 if na == nb else difflib.SequenceMatcher(None, na, nb).ratio()
            # "Молоко 2.5%" vs "Молоко 3.2%" are different products: digits must match.
            if ratio >= FUZZY and re.findall(r"\d+", na) == re.findall(r"\d+", nb):
                seen.add((a.pk, b.pk))
                out.append(Found("product_duplicate", "warn", "product", a.pk, a.name,
                                 f"Схожий продукт: «{b.name}». Якщо це одне й те саме — залиште один.",
                                 {"ids": [a.pk, b.pk], "names": [a.name, b.name]}))
    return out


def check_dishes(dishes) -> list[Found]:
    out = []
    for d in with_dish_nutrition(dishes):
        ings = list(d.ingredients.all())
        if not ings:
            out.append(Found("dish_no_ingredients", "error", "dish", d.pk, d.name, "У картці немає жодного продукту."))
            continue
        bad = [i.product.name for i in ings if i.netto_g > i.brutto_g]
        if bad:
            out.append(Found("dish_netto_gt_brutto", "error", "dish", d.pk, d.name,
                             "Нетто більше брутто: " + ", ".join(bad) + "."))
        netto = sum((i.netto_g for i in ings), Decimal(0))
        if netto and d.output_g:
            ratio = d.output_g / netto
            if not (OUTPUT_RANGE[0] <= ratio <= OUTPUT_RANGE[1]):
                out.append(Found("dish_output", "warn", "dish", d.pk, d.name,
                                 f"Вихід {d.output_g:g} г при сумі нетто {netto:g} г (×{ratio:.2f}). Перевірте вихід або норми.",
                                 {"output_g": str(d.output_g), "netto_g": str(netto)}))
        if not d.technology.strip():
            out.append(Found("dish_no_technology", "info", "dish", d.pk, d.name, "Не заповнено технологію приготування."))
    return out


def check_menus(menus) -> list[Found]:
    from apps.menus.views import NORM_FIELDS

    out = []
    menus = with_menu_nutrition(menus)
    for m in menus:
        problems = []
        for fld, label in NORM_FIELDS:
            low, high = m.diet.norm(fld)
            value = getattr(m, f"n_{fld}")
            if norm_status(value, low, high) == "bad":
                target = f"{low:g}–{high:g}" if low is not None and high is not None else f"{low or high:g}"
                problems.append(f"{label} {value:.0f} (норма {target})")
        if problems:
            out.append(Found("menu_norm", "warn", "menu", m.number, f"Меню №{m.number} ({m.diet.code})",
                             "; ".join(problems) + ".", {"diet": m.diet.code}))
    for m in menus:
        allowed = set(m.diet.dishes.values_list("pk", flat=True))
        wrong = sorted({i.dish.name for i in m.items.all() if i.dish_id not in allowed})
        if wrong:
            out.append(Found("menu_dish_not_allowed", "error", "menu", m.number, f"Меню №{m.number} ({m.diet.code})",
                             "Не дозволені для дієти: " + ", ".join(wrong) + "."))
    return out


def run_rules() -> list[Found]:
    from apps.menus.models import Menu

    products = list(Product.objects.filter(is_active=True).order_by("name"))
    dishes = Dish.objects.filter(is_active=True).prefetch_related("ingredients__product").order_by("card_no")
    menus = Menu.objects.filter(is_active=True).select_related("diet").prefetch_related("items__dish").order_by("number")
    return check_products(products) + check_dishes(dishes) + check_menus(menus)


# ------------------------------------------------------------------ persistence
@transaction.atomic
def store(found: list[Found], *, sources=("rule",)) -> dict:
    """Upsert this run's findings for the given sources. Returns counts {new, reopened, resolved, open}."""
    now = timezone.now()
    existing = {(f.kind, f.obj_type, f.obj_id): f for f in ReferenceFinding.objects.select_for_update()
                .filter(source__in=sources)}
    keys, new, reopened = set(), 0, 0
    for f in found:
        key = (f.kind, f.obj_type, f.obj_id)
        if key in keys:
            continue
        keys.add(key)
        row = existing.get(key)
        if row is None:
            ReferenceFinding.objects.create(kind=f.kind, severity=f.severity, obj_type=f.obj_type, obj_id=f.obj_id,
                                            title=f.title, message=f.message, details=f.details, source=f.source)
            new += 1
            continue
        row.title, row.message, row.details, row.severity = f.title, f.message, f.details, f.severity
        if row.status == ReferenceFinding.Status.RESOLVED:   # came back
            row.status, row.resolved_at = ReferenceFinding.Status.OPEN, None
            reopened += 1
        row.save()
    gone = [r.pk for k, r in existing.items() if k not in keys and r.status == ReferenceFinding.Status.OPEN]
    ReferenceFinding.objects.filter(pk__in=gone).update(status=ReferenceFinding.Status.RESOLVED, resolved_at=now)
    return {"new": new, "reopened": reopened, "resolved": len(gone),
            "open": ReferenceFinding.objects.filter(status=ReferenceFinding.Status.OPEN).count()}


def run_audit(*, with_ai=False, user=None) -> dict:
    counts = store(run_rules(), sources=("rule",))
    counts["ai"] = None
    if with_ai:
        from apps.assist import client
        from apps.assist.features import ai_duplicate_groups

        if client.is_available():
            try:
                ai_found = ai_duplicate_groups(user=user)
            except (client.AIError, client.AILimitExceeded) as exc:
                counts["ai"] = f"ШІ: {exc}"
            else:
                ai_counts = store(ai_found, sources=("ai",))
                counts["new"] += ai_counts["new"]
                counts["open"] = ai_counts["open"]
                counts["ai"] = "ok"
    return counts


def object_url(finding):
    from django.urls import reverse

    if finding.obj_type == "product":
        return reverse("catalog:product_edit", args=[finding.obj_id])
    if finding.obj_type == "dish":
        return reverse("catalog:dish_detail", args=[finding.obj_id])
    if finding.obj_type == "menu":
        return reverse("menus:detail", args=[finding.obj_id])
    return ""
