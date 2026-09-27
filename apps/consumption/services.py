"""Freeze the consumption of an approved plan (CLAUDE.md invariant 5)."""
from collections import OrderedDict
from decimal import Decimal

from django.db import transaction

from apps.core.text import uk_sort_key

from .calc import PlanInput, compute_lines
from .models import ConsumptionSnapshotLine


class AlreadyFrozen(Exception):
    pass


@transaction.atomic
def freeze(plan):
    if ConsumptionSnapshotLine.objects.filter(plan=plan).exists():
        raise AlreadyFrozen(f"Розхід для {plan} уже зафіксовано")
    entries = [PlanInput(e.menu.number, e.portions) for e in plan.entries.select_related("menu") if e.menu_id]
    lines = compute_lines(entries)
    ConsumptionSnapshotLine.objects.bulk_create([
        ConsumptionSnapshotLine(
            plan=plan,
            diet_code=line.diet_code,
            menu_number=line.menu_number,
            meal_slot=line.meal_slot,
            dish_card_no=line.dish_card_no,
            dish_name=line.dish_name,
            dish_output_g=line.dish_output_g,
            product_id=line.product_id,
            product_name=line.product_name,
            portions=line.portions,
            brutto_per_portion_g=line.brutto_pp,
            netto_per_portion_g=line.netto_pp,
            brutto_total_g=line.brutto_total,
            netto_total_g=line.netto_total,
            kcal_total=line.kcal_total.quantize(Decimal("0.01")),
        )
        for line in lines
    ])
    return len(lines)


def snapshot_totals(plan):
    """[(product_name, brutto_g, netto_g)] from the frozen snapshot, by product name."""
    acc = OrderedDict()
    for row in ConsumptionSnapshotLine.objects.filter(plan=plan).order_by("product_name").values(
        "product_name", "brutto_total_g", "netto_total_g"
    ):
        b, n = acc.get(row["product_name"], (Decimal(0), Decimal(0)))
        acc[row["product_name"]] = (b + row["brutto_total_g"], n + row["netto_total_g"])
    return [(name, b, n) for name, (b, n) in acc.items()]


# ====================================================================== S4-01 kitchen
KG = Decimal(1000)
DEVIATION_REASON_PCT = Decimal(10)   # docs/03_task_engine.md: deviation > 10% needs a reason


def slot_order():
    from apps.catalog.models import MealSlot

    return {value: i for i, (value, _) in enumerate(MealSlot.choices)}


def slot_label(value):
    from apps.catalog.models import MealSlot

    return dict(MealSlot.choices).get(value, value)


def kitchen_board(plan):
    """[{slot, label, dishes: [{card_no, name, output_g, by_diet: {code: portions}, portions, output_total_g}]}]

    Built only from the frozen snapshot, so the kitchen cooks exactly what was approved.
    """
    seen, dishes = set(), {}
    rows = ConsumptionSnapshotLine.objects.filter(plan=plan).values_list(
        "meal_slot", "dish_card_no", "dish_name", "dish_output_g", "diet_code", "menu_number", "portions"
    )
    for slot, card_no, name, output_g, diet_code, menu_number, portions in rows:
        key = (slot, card_no, diet_code, menu_number)
        if key in seen:           # one row per ingredient; portions are per dish
            continue
        seen.add(key)
        dish = dishes.setdefault((slot, card_no), {
            "card_no": card_no, "name": name, "output_g": output_g, "by_diet": {}, "portions": 0,
        })
        dish["by_diet"][diet_code] = dish["by_diet"].get(diet_code, 0) + portions
        dish["portions"] += portions
    order = slot_order()
    board = {}
    for (slot, _), dish in sorted(dishes.items(), key=lambda kv: (order.get(kv[0][0], 99), uk_sort_key(kv[1]["name"]))):
        dish["output_total_g"] = (dish["output_g"] or 0) * dish["portions"]
        board.setdefault(slot, []).append(dish)
    return [{"slot": s, "label": slot_label(s), "dishes": d} for s, d in board.items()]


def scaled_card(plan, slot, card_no, portions=None):
    """Card as frozen at approval, multiplied by `portions` (default: planned portions of that dish)."""
    lines = list(ConsumptionSnapshotLine.objects.filter(plan=plan, meal_slot=slot, dish_card_no=card_no)
                 .order_by("id"))
    if not lines:
        return None
    planned = sum({(l.diet_code, l.menu_number): l.portions for l in lines}.values())
    n = planned if portions is None else portions
    products = OrderedDict()
    for l in lines:
        products.setdefault(l.product_id, (l.product_name, l.brutto_per_portion_g, l.netto_per_portion_g))
    return {
        "slot": slot, "label": slot_label(slot), "card_no": card_no, "name": lines[0].dish_name,
        "output_g": lines[0].dish_output_g, "planned": planned, "portions": n,
        "rows": [
            {"product": name, "brutto_pp": b, "netto_pp": nt, "brutto": b * n, "netto": nt * n}
            for name, b, nt in products.values()
        ],
    }


def snapshot_by_product(plan):
    """{product_id: (product_name, brutto_g)} from the frozen snapshot."""
    acc = OrderedDict()
    for pid, name, b in ConsumptionSnapshotLine.objects.filter(plan=plan).order_by("product_name").values_list(
        "product_id", "product_name", "brutto_total_g"
    ):
        prev = acc.get(pid, (name, Decimal(0)))
        acc[pid] = (name, prev[1] + b)
    return acc


def cooking_task(plan):
    from apps.workflow.definitions import COOKING
    from apps.workflow.models import Task

    return Task.objects.filter(type=COOKING, service_date=plan.service_date).first()


@transaction.atomic
def ensure_actuals(plan):
    """The fact table starts equal to the plan; the chef changes only what differed."""
    from .models import ActualConsumption

    if not plan.is_frozen or ActualConsumption.objects.filter(plan=plan).exists():
        return
    ActualConsumption.objects.bulk_create([
        ActualConsumption(plan=plan, product_id=pid, product_name=name, planned_brutto_g=b, actual_brutto_g=b)
        for pid, (name, b) in snapshot_by_product(plan).items()
    ])


def kitchen_editable(plan, user):
    from apps.workflow import services as wf

    task = cooking_task(plan)
    return task is not None and wf.can(user, task, "submit")


def link_subject(task, obj):
    if task is not None and (task.content_type_id is None or task.object_id != obj.pk):
        task.subject = obj
        task.save(update_fields=["content_type", "object_id"])


def parse_kg(raw, label):
    """'1,250' / '1.25' kg → grams (Decimal). Kitchen scales show kilograms."""
    from django.core.exceptions import ValidationError

    raw = (raw or "").strip().replace(" ", "").replace("\u00a0", "").replace("\u202f", "").replace(",", ".")
    if raw == "":
        return None
    try:
        value = Decimal(raw)
    except ArithmeticError as exc:
        raise ValidationError(f"{label}: вкажіть число в кілограмах.") from exc
    if value < 0 or not value.is_finite():
        raise ValidationError(f"{label}: кількість не може бути від'ємною.")
    return (value * KG).quantize(Decimal("0.01"))


@transaction.atomic
def save_actuals(plan, user, values: dict, extra=None):
    """values: {actual_id: (grams|None, reason)}; extra: (product, grams, reason) — a replacement product."""
    from django.core.exceptions import PermissionDenied

    from apps.workflow import services as wf

    from .models import ActualConsumption

    if not kitchen_editable(plan, user):
        raise PermissionDenied("Факт зараз не можна редагувати.")
    for actual in ActualConsumption.objects.select_for_update().filter(plan=plan, pk__in=values):
        grams, reason = values[actual.pk]
        if grams is not None:
            actual.actual_brutto_g = grams
        actual.reason = (reason or "").strip()[:200]
        actual.updated_by = user
        actual.save(update_fields=["actual_brutto_g", "reason", "updated_by", "updated_at"])
    if extra:
        product, grams, reason = extra
        actual, created = ActualConsumption.objects.get_or_create(
            plan=plan, product=product,
            defaults={"product_name": product.name, "planned_brutto_g": 0, "actual_brutto_g": grams,
                      "reason": (reason or "").strip()[:200], "updated_by": user},
        )
        if not created:
            actual.actual_brutto_g = grams
            actual.reason = (reason or actual.reason).strip()[:200]
            actual.updated_by = user
            actual.save()
    task = cooking_task(plan)
    if task.status == "pending":
        wf.start(task, user)


def deviation_errors(plan) -> list[str]:
    from .models import ActualConsumption

    errors = []
    for a in ActualConsumption.objects.filter(plan=plan):
        dev = a.deviation_pct
        if dev is not None and abs(dev) > DEVIATION_REASON_PCT and not a.reason.strip():
            errors.append(f"{a.product_name}: відхилення {dev:+.0f}% — вкажіть причину.")
    return errors


def validate_cooking_task(task) -> list[str]:
    from apps.planning.models import DailyPlan

    plan = DailyPlan.objects.filter(service_date=task.service_date).first()
    if plan is None or not plan.is_frozen:
        return ["План дня ще не затверджено."]
    ensure_actuals(plan)
    return deviation_errors(plan)


# ====================================================================== S4-03 write-off
def write_off_task(service_date):
    from apps.workflow.definitions import WRITE_OFF
    from apps.workflow.models import Task

    return Task.objects.filter(type=WRITE_OFF, service_date=service_date).first()


def open_write_off(plan):
    from .models import WriteOff

    task = write_off_task(plan.service_date)
    wo, _ = WriteOff.objects.get_or_create(plan=plan, defaults={"task": task})
    if task and wo.task_id != task.pk:
        wo.task = task
        wo.save(update_fields=["task"])
    link_subject(task, wo)
    return wo


def cost_of(grams, price_per_kg):
    if price_per_kg is None:
        return None
    return (Decimal(grams) / KG * price_per_kg).quantize(Decimal("0.01"))


def live_write_off_rows(plan):
    """Quantities: the chef's fact when it exists, else the frozen plan. Prices: current catalog."""
    from apps.catalog.models import Product

    from .models import ActualConsumption

    actuals = list(ActualConsumption.objects.filter(plan=plan))
    if actuals:
        base = [(a.product_id, a.product_name, a.planned_brutto_g, a.actual_brutto_g, a.reason) for a in actuals]
    else:
        base = [(pid, name, b, b, "") for pid, (name, b) in snapshot_by_product(plan).items()]
    prices = dict(Product.objects.filter(pk__in=[r[0] for r in base]).values_list("pk", "price_per_kg"))
    rows = []
    for pid, name, planned, qty, reason in sorted(base, key=lambda r: uk_sort_key(r[1])):
        if not qty and not planned:
            continue
        price = prices.get(pid)
        rows.append({"product_id": pid, "product_name": name, "planned_brutto_g": planned, "quantity_g": qty,
                     "price_per_kg": price, "cost": cost_of(qty, price), "reason": reason})
    return rows


def write_off_rows(wo):
    """Frozen lines once submitted/approved; live calculation while the accountant works."""
    if wo.task is not None and wo.task.status in ("submitted", "approved"):
        return [
            {"product_id": l.product_id, "product_name": l.product_name, "planned_brutto_g": l.planned_brutto_g,
             "quantity_g": l.quantity_g, "price_per_kg": l.price_per_kg, "cost": l.cost, "reason": l.reason}
            for l in wo.lines.all()
        ]
    return live_write_off_rows(wo.plan)


@transaction.atomic
def prepare_lines(wo):
    from django.utils import timezone

    from .models import WriteOffLine

    rows = live_write_off_rows(wo.plan)
    wo.lines.all().delete()
    WriteOffLine.objects.bulk_create([
        WriteOffLine(write_off=wo, product_id=r["product_id"], product_name=r["product_name"],
                     planned_brutto_g=r["planned_brutto_g"], quantity_g=r["quantity_g"],
                     price_per_kg=r["price_per_kg"], cost=r["cost"], reason=r["reason"])
        for r in rows
    ])
    wo.total_cost = sum((r["cost"] for r in rows), Decimal(0))
    wo.prepared_at = timezone.now()
    wo.save(update_fields=["total_cost", "prepared_at"])


def validate_write_off_task(task) -> list[str]:
    """Runs inside submit's transaction: freezes the lines with today's prices, or refuses."""
    from apps.planning.models import DailyPlan

    plan = DailyPlan.objects.filter(service_date=task.service_date).first()
    if plan is None or not plan.is_frozen:
        return ["План дня не затверджено — списувати нічого."]
    wo = open_write_off(plan)
    missing = [r["product_name"] for r in live_write_off_rows(plan) if r["price_per_kg"] is None]
    if missing:
        return [f"Немає ціни за кг: {', '.join(missing[:10])}{' …' if len(missing) > 10 else ''}."]
    prepare_lines(wo)
    return []


def approve_write_off(task):
    from django.utils import timezone

    from .models import WriteOff

    WriteOff.objects.filter(task=task).update(approved_at=timezone.now())


def write_off_editable(wo, user):
    from apps.workflow import services as wf

    return wo.task is not None and wf.can(user, wo.task, "submit")


@transaction.atomic
def set_prices(wo, user, prices: dict):
    """Accountant fills missing/changed prices right from the write-off screen (history is kept)."""
    from django.core.exceptions import PermissionDenied

    from apps.catalog.models import Product
    from apps.core.navigation import PRICE_EDITORS

    if not (write_off_editable(wo, user) and user.has_role(*PRICE_EDITORS)):
        raise PermissionDenied("Ціни зараз не можна змінювати.")
    changed = 0
    for product in Product.objects.filter(pk__in=prices):
        if prices[product.pk] != product.price_per_kg:
            product.price_per_kg = prices[product.pk]
            product.save()
            changed += 1
    return changed


# ====================================================================== S4-02 period report
GROUPS = {"product": "за продуктами", "diet": "за дієтами", "dish": "за стравами"}


def period_consumption(date_from, date_to, group="product"):
    """Frozen snapshot lines of approved plans in [date_from, date_to], grouped.

    Returns {"groups": [{"label", "rows": [{product_id, product_name, brutto_g, netto_g, price_per_kg, cost}],
    "brutto_g", "cost"}], "brutto_g", "cost", "days": [dates], "missing_prices": bool}.
    Cost is at current catalog prices (the write-off holds the prices used for accounting).
    """
    from apps.catalog.models import Product

    qs = ConsumptionSnapshotLine.objects.filter(plan__service_date__range=(date_from, date_to))
    days = sorted(set(qs.values_list("plan__service_date", flat=True)))
    key_field = {"product": None, "diet": "diet_code", "dish": "dish_name"}[group]
    fields = ["product_id", "product_name", "brutto_total_g", "netto_total_g"] + ([key_field] if key_field else [])
    acc = OrderedDict()
    for row in qs.order_by(*( [key_field] if key_field else []), "product_name").values(*fields):
        label = row[key_field] if key_field else ""
        bucket = acc.setdefault(label, OrderedDict())
        prev = bucket.get(row["product_id"], [row["product_name"], Decimal(0), Decimal(0)])
        prev[1] += row["brutto_total_g"]
        prev[2] += row["netto_total_g"]
        bucket[row["product_id"]] = prev
    prices = dict(Product.objects.filter(
        pk__in={pid for b in acc.values() for pid in b}).values_list("pk", "price_per_kg"))
    groups, total_b, total_c, missing = [], Decimal(0), Decimal(0), False
    for label, bucket in acc.items():
        rows = []
        for pid, (name, b, n) in sorted(bucket.items(), key=lambda kv: uk_sort_key(kv[1][0])):
            price = prices.get(pid)
            cost = cost_of(b, price)
            missing |= price is None
            rows.append({"product_id": pid, "product_name": name, "brutto_g": b, "netto_g": n,
                         "price_per_kg": price, "cost": cost})
        g_b = sum((r["brutto_g"] for r in rows), Decimal(0))
        g_c = sum((r["cost"] or 0 for r in rows), Decimal(0))
        groups.append({"label": label, "rows": rows, "brutto_g": g_b, "cost": g_c})
        total_b += g_b
        total_c += g_c
    return {"groups": groups, "brutto_g": total_b, "cost": total_c, "days": days, "missing_prices": missing}
