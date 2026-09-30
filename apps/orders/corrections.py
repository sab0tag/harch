"""S7-01: order corrections — «добавка до вимоги» (docs/05_sprints.md, docs/09_discovery.md).

A department corrects its approved order: ± portions per diet from a meal on, with a reason. A transfer
is one correction by the receiving department: + for it, − for the department the patients came from.
The dietician approves. The approved order is never edited; instead the correction becomes extra lines
of the day's frozen consumption («Додаткова вимога», ConsumptionSnapshotLine.correction) — right away if
the plan is already frozen, otherwise when it is. The kitchen screen, the kitchen fact, the write-off and
form З-4а read those lines like any other.
"""
from datetime import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounts.models import Role
from apps.catalog.models import Diet, MealSlot
from apps.workflow import notify as n
from apps.workflow import services as wf
from apps.workflow.definitions import COOKING, ORDER_CORRECTION, ORDER_SUBMISSION
from apps.workflow.models import Task, TaskStatus

from .models import DailyOrder, OrderCorrection, OrderCorrectionLine
from .services import approved_portions, portions_by_diet

SLOTS = [value for value, _ in MealSlot.choices]          # in serving order
SLOT_LABELS = dict(MealSlot.choices)


def _cutoff():
    h, m = (int(x) for x in settings.CORRECTION_LUNCH_CUTOFF.split(":"))
    return time(h, m)


def allowed_meals(department, day, now=None):
    """(meals a correction may start from, None) or ([], why it cannot be made now)."""
    now = timezone.localtime(now or timezone.now())
    if day < now.date():
        return [], "Дата минула."
    order_task = Task.objects.filter(type=ORDER_SUBMISSION, service_date=day, department=department).first()
    if order_task is None or order_task.status != TaskStatus.APPROVED:
        return [], "Замовлення відділення на цю дату ще не затверджене — змініть саме замовлення."
    cooking = Task.objects.filter(type=COOKING, service_date=day).first()
    if cooking is not None and cooking.status in (TaskStatus.SUBMITTED, TaskStatus.APPROVED):
        return [], "Факт кухні за цей день уже подано — коригувати пізно."
    if day > now.date():
        return SLOTS, None
    first = MealSlot.LUNCH if now.time() < _cutoff() else MealSlot.DINNER
    return SLOTS[SLOTS.index(first):], None


def available_diets(day):
    """Diets the day is cooked for: a correction can only add or remove portions of those."""
    return Diet.objects.filter(pk__in=approved_portions(day)).order_by("sort_order", "code")


def department_portions(department, day, exclude=None):
    """{diet_id: portions} the department has for the day: approved order + approved corrections."""
    order = DailyOrder.objects.filter(department=department, service_date=day,
                                      task__status=TaskStatus.APPROVED).first()
    result = portions_by_diet(order) if order else {}
    lines = OrderCorrectionLine.objects.filter(department=department, correction__service_date=day,
                                               correction__task__status=TaskStatus.APPROVED)
    if exclude is not None:
        lines = lines.exclude(correction=exclude)
    for diet_id, total in lines.values("diet_id").annotate(total=Sum("delta")).values_list("diet_id", "total"):
        result[diet_id] = result.get(diet_id, 0) + total
    return result


def can_create(user, department):
    """Only the department's head nurse: she knows who was admitted, discharged or transferred.
    The admin sees corrections (the order page, «Стан дня») but does not submit them."""
    return user.role == Role.DEPT_NURSE and user.department_id == department.pk


@transaction.atomic
def open_correction(department, day, user):
    if not can_create(user, department):
        raise PermissionDenied("Коригування подає старша медсестра відділення.")
    meals, why = allowed_meals(department, day)
    if why:
        raise ValidationError(why)
    correction = OrderCorrection.objects.create(department=department, service_date=day, from_meal=meals[0],
                                                created_by=user)
    correction.task = wf.create_task(ORDER_CORRECTION, day, department=department, subject=correction)
    correction.save(update_fields=["task"])
    return correction


def is_editable_by(correction, user):
    return correction.task is not None and wf.can(user, correction.task, "submit")


@transaction.atomic
def save_correction(correction, user, deltas, *, from_meal, reason, transfer_from=None, note=""):
    """deltas: {diet_id: ± portions} for the correcting department."""
    if not is_editable_by(correction, user):
        raise PermissionDenied("Коригування зараз не можна редагувати.")
    dept, day = correction.department, correction.service_date
    meals, why = allowed_meals(dept, day)
    if why:
        raise ValidationError(why)
    if from_meal not in meals:
        raise ValidationError("З цього прийому їжі вже пізно. Можна: " + ", ".join(SLOT_LABELS[m].lower() for m in meals) + ".")
    if reason not in OrderCorrection.Reason.values:
        raise ValidationError("Оберіть причину.")
    deltas = {d: v for d, v in deltas.items() if v}
    if not deltas:
        raise ValidationError("Вкажіть + або − порції хоча б для однієї дієти.")
    diets = {d.pk: d for d in available_diets(day)}
    unknown = set(deltas) - set(diets)
    if unknown:
        codes = ", ".join(d.code for d in Diet.objects.filter(pk__in=unknown))
        raise ValidationError(f"Дієти {codes} на цей день немає в плані харчоблоку — узгодьте з дієтсестрою.")

    errors = []
    if reason == OrderCorrection.Reason.TRANSFER:
        if transfer_from is None or transfer_from.pk == dept.pk:
            errors.append("Оберіть відділення, з якого переведено пацієнтів.")
        if any(v < 0 for v in deltas.values()):
            errors.append("При переведенні вкажіть, скільки пацієнтів прийнято (+); з іншого відділення їх знімуть самі.")
    else:
        transfer_from = None
    have = department_portions(dept, day, exclude=correction)
    for diet_id, v in deltas.items():
        if have.get(diet_id, 0) + v < 0:
            errors.append(f"{diets[diet_id].code}: у відділенні {have.get(diet_id, 0)} порц. — не можна зняти {-v}.")
    if transfer_from is not None and not errors:
        sender = department_portions(transfer_from, day, exclude=correction)
        for diet_id, v in deltas.items():
            if sender.get(diet_id, 0) - v < 0:
                errors.append(f"{diets[diet_id].code}: у відділенні «{transfer_from}» лише {sender.get(diet_id, 0)} порц.")
    if errors:
        raise ValidationError(errors)

    correction.from_meal, correction.reason, correction.transfer_from = from_meal, reason, transfer_from
    correction.note = note
    correction.save(update_fields=["from_meal", "reason", "transfer_from", "note", "updated_at"])
    correction.lines.all().delete()
    lines = [OrderCorrectionLine(correction=correction, department=dept, diet_id=d, delta=v) for d, v in deltas.items()]
    if transfer_from is not None:
        lines += [OrderCorrectionLine(correction=correction, department=transfer_from, diet_id=d, delta=-v)
                  for d, v in deltas.items()]
    OrderCorrectionLine.objects.bulk_create(lines)
    if correction.task.status == TaskStatus.PENDING:
        wf.start(correction.task, user)
    return correction


def summary_text(correction):
    own = [l for l in correction.lines.all() if l.department_id == correction.department_id]
    parts = ", ".join(f"{l.diet.code} {l.delta:+d}" for l in own)
    source = f" з «{correction.transfer_from}»" if correction.transfer_from_id else ""
    return f"{correction.get_reason_display()}{source}: {parts}, {correction.from_meal_phrase}"


@transaction.atomic
def submit_correction(correction, user, deltas, **fields):
    save_correction(correction, user, deltas, **fields)
    return wf.submit(correction.task, user, summary_text(correction))


def validate_correction_task(task):
    """Registry hook before submit: the correction still makes sense now."""
    correction = OrderCorrection.objects.filter(task=task).first()
    if correction is None or not correction.lines.exists():
        return ["Коригування ще не заповнене."]
    meals, why = allowed_meals(correction.department, correction.service_date)
    if why:
        return [why]
    if correction.from_meal not in meals:
        return ["З обраного прийому їжі вже пізно — оберіть наступний і збережіть."]
    return []


# ------------------------------------------------------------------ into the day's consumption
def add_extra_lines(correction, plan):
    """«Додаткова вимога»: products for the corrected portions from `from_meal` on, by the plan's menus."""
    from decimal import Decimal

    from apps.consumption.calc import PlanInput, compute_lines
    from apps.consumption.models import ActualConsumption, ConsumptionSnapshotLine

    menus = {e.diet_id: e.menu for e in plan.entries.select_related("menu") if e.menu_id}
    start = SLOTS.index(correction.from_meal)
    new = []
    for line in correction.lines.select_related("diet"):
        menu = menus.get(line.diet_id)
        if menu is None:
            raise ValidationError(f"У плані на {plan.service_date:%d.%m} немає меню для дієти {line.diet.code}.")
        sign = 1 if line.delta > 0 else -1
        for l in compute_lines([PlanInput(menu.number, abs(line.delta))]):
            if l.meal_slot in SLOTS and SLOTS.index(l.meal_slot) < start:
                continue
            new.append(ConsumptionSnapshotLine(
                plan=plan, correction=correction, diet_code=l.diet_code, menu_number=l.menu_number,
                meal_slot=l.meal_slot, dish_card_no=l.dish_card_no, dish_name=l.dish_name,
                dish_output_g=l.dish_output_g, product_id=l.product_id, product_name=l.product_name,
                portions=sign * l.portions, brutto_per_portion_g=l.brutto_pp, netto_per_portion_g=l.netto_pp,
                brutto_total_g=sign * l.brutto_total, netto_total_g=sign * l.netto_total,
                kcal_total=(sign * l.kcal_total).quantize(Decimal("0.01")),
            ))
    ConsumptionSnapshotLine.objects.bulk_create(new)
    # The chef's fact table (if already started) follows: planned and fact grow by the same amount.
    if ActualConsumption.objects.filter(plan=plan).exists():
        by_product = {}
        for l in new:
            name, grams = by_product.get(l.product_id, (l.product_name, Decimal(0)))
            by_product[l.product_id] = (name, grams + l.brutto_total_g)
        for pid, (name, grams) in by_product.items():
            actual, _ = ActualConsumption.objects.get_or_create(
                plan=plan, product_id=pid,
                defaults={"product_name": name, "planned_brutto_g": 0, "actual_brutto_g": 0})
            actual.planned_brutto_g += grams
            actual.actual_brutto_g = max(Decimal(0), actual.actual_brutto_g + grams)
            actual.save(update_fields=["planned_brutto_g", "actual_brutto_g", "updated_at"])
    correction.applied_at = timezone.now()
    correction.save(update_fields=["applied_at"])
    return len(new)


def apply_approved(task):
    """Registry hook: an approved correction goes into a frozen plan now, else waits for the freeze."""
    from apps.planning.models import DailyPlan

    correction = OrderCorrection.objects.select_for_update().get(task=task)
    meals, why = allowed_meals(correction.department, correction.service_date)
    if why and "Факт кухні" in why:
        raise ValidationError(why)
    plan = DailyPlan.objects.filter(service_date=correction.service_date).first()
    if plan is not None and plan.is_frozen and correction.applied_at is None:
        add_extra_lines(correction, plan)
    if correction.transfer_from_id:
        nurses = get_user_model().objects.filter(role=Role.DEPT_NURSE, department=correction.transfer_from,
                                                 is_active=True)
        moved = sum(l.delta for l in correction.lines.all() if l.department_id == correction.department_id)
        n.notify(nurses, task, f"Переведення: {correction.department} прийняло {moved} з вашого відділення",
                 f"На {correction.service_date:%d.%m} у вашому відділенні знято {moved} порц. "
                 f"{correction.from_meal_phrase}.")


def apply_pending(plan):
    """At the plan's freeze: corrections approved while it was still being planned."""
    pending = OrderCorrection.objects.filter(service_date=plan.service_date, applied_at__isnull=True,
                                             task__status=TaskStatus.APPROVED).order_by("pk")
    for correction in pending:
        add_extra_lines(correction, plan)


def corrections_for(day, department=None):
    qs = OrderCorrection.objects.filter(service_date=day).select_related(
        "department", "transfer_from", "task").prefetch_related("lines__diet", "lines__department")
    if department is not None:
        qs = qs.filter(department=department) | qs.filter(transfer_from=department)
    return qs.exclude(task__status=TaskStatus.CANCELLED).order_by("created_at")
