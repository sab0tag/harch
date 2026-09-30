"""Department orders: open, save, submit (CLAUDE.md invariants 3, 8, 9)."""
from collections import OrderedDict
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.catalog.models import Diet
from apps.workflow import services as wf
from apps.workflow.definitions import ORDER_SUBMISSION
from apps.workflow.models import TaskStatus

from .models import DailyOrder, DailyOrderLine


OPEN_AHEAD_DAYS = 7    # a weekend's orders are handed in on Friday; a week covers any holiday stretch


def opens_on(service_date):
    """The first day an order for service_date can be opened. Browsing further ahead must not create tasks:
    each lands in the nurse's list with a notification, for a day nobody is planning yet."""
    return service_date - timedelta(days=OPEN_AHEAD_DAYS)


def default_service_date():
    return timezone.localdate() + timedelta(days=1)


def orderable_diets():
    """Diets that can be planned (have their own menus), department default first in UI."""
    return Diet.objects.filter(is_active=True, has_own_menus=True).order_by("sort_order", "code")


def open_order(department, service_date):
    """Order + its task for (department, date); creates both if the scheduler hasn't yet."""
    task, _ = wf.ensure_task(ORDER_SUBMISSION, service_date, department=department)
    order, created = DailyOrder.objects.get_or_create(
        department=department, service_date=service_date, defaults={"task": task}
    )
    if order.task_id != task.pk:
        order.task = task
        order.save(update_fields=["task"])
    if task.object_id != order.pk:
        task.subject = order
        task.save(update_fields=["content_type", "object_id"])
    return order


def is_editable_by(order, user):
    return order.task is not None and wf.can(user, order.task, "submit")


def portions_by_diet(order):
    return {line.diet_id: line.portions for line in order.lines.all()}


@transaction.atomic
def save_order(order, user, portions: dict, *, no_patients=False, note=""):
    """portions: {diet_id: int}. Zero/empty removes the line."""
    if not is_editable_by(order, user):
        raise PermissionDenied("Замовлення зараз не можна редагувати.")
    allowed = set(orderable_diets().values_list("pk", flat=True))
    unknown = set(portions) - allowed
    if unknown:
        raise ValidationError("Невідома дієта у замовленні.")
    if any(v < 0 for v in portions.values()):
        raise ValidationError("Кількість порцій не може бути від'ємною.")
    order.lines.exclude(diet_id__in=[d for d, v in portions.items() if v > 0]).delete()
    for diet_id, value in portions.items():
        if value > 0:
            DailyOrderLine.objects.update_or_create(order=order, diet_id=diet_id, defaults={"portions": value})
    order.no_patients = no_patients
    order.note = note
    order.updated_by = user
    order.save(update_fields=["no_patients", "note", "updated_by", "updated_at"])
    if order.task.status == TaskStatus.PENDING:
        wf.start(order.task, user)
    return order


@transaction.atomic
def submit_order(order, user, portions: dict, *, no_patients=False, note=""):
    save_order(order, user, portions, no_patients=no_patients, note=note)
    total = sum(v for v in portions.values() if v > 0)
    comment = "Пацієнтів немає" if no_patients else f"Порцій: {total}"
    return wf.submit(order.task, user, comment)


def validate_order_task(task) -> list[str]:
    """Registry hook: what must be true before an order can be submitted."""
    order = DailyOrder.objects.filter(task=task).first()
    if order is None:
        return ["Замовлення ще не заповнене."]
    total = order.lines.aggregate(s=Sum("portions"))["s"] or 0
    if order.no_patients and total:
        return ["Позначено «пацієнтів немає», але вказано порції."]
    if not order.no_patients and total == 0:
        return ["Вкажіть порції хоча б для однієї дієти або позначте «пацієнтів немає»."]
    return []


def approved_portions(service_date, *, include_late=False):
    """{diet_id: portions} over approved orders of the day — the input to the day plan.

    A late order (approved after the plan left the dietician) goes into consumption as a correction
    (corrections.take_late_order), so it is not part of the plan's base unless include_late."""
    lines = DailyOrderLine.objects.filter(order__service_date=service_date, order__task__status=TaskStatus.APPROVED)
    if not include_late:
        lines = lines.filter(order__task__order_correction__isnull=True)
    rows = (
        lines
        .values("diet_id")
        .annotate(total=Sum("portions"))
    )
    return {r["diet_id"]: r["total"] for r in rows if r["total"]}


def summary(service_date, departments):
    """Rows for the dietician's matrix: department, order, task, {diet_id: portions}."""
    orders = {
        o.department_id: o
        for o in DailyOrder.objects.filter(service_date=service_date).select_related("task").prefetch_related("lines")
    }
    from apps.workflow.models import Task

    tasks = {t.department_id: t for t in Task.objects.filter(type=ORDER_SUBMISSION, service_date=service_date)}
    rows = []
    for dept in departments:
        order = orders.get(dept.pk)
        rows.append(OrderedDict(
            department=dept,
            order=order,
            task=tasks.get(dept.pk),
            portions=portions_by_diet(order) if order else {},
        ))
    return rows
