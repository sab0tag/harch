"""The four task types of a service day (docs/03_task_engine.md, «Типы задач v0.1»).

замовлення (кожне відділення) → план меню → приготування → списання
                                 ↘ коригування замовлення (S7-01, скільки завгодно на день)

Subjects: order → DailyOrder, menu_planning and cooking → DailyPlan, write_off → WriteOff,
order_correction → OrderCorrection.
"""
from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import Role

from .models import CLOSED, Task
from .registry import TaskDefinition, register

ORDER_SUBMISSION = "order_submission"
MENU_PLANNING = "menu_planning"
COOKING = "cooking"
WRITE_OFF = "write_off"
ORDER_CORRECTION = "order_correction"  # models.REPEATABLE_TYPES


def _at(day, hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return timezone.make_aware(datetime.combine(day, time(h, m)), timezone.get_default_timezone())


def previous_workday(day):
    """The last Monday–Friday before `day` (holidays: not yet)."""
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def next_workday(day):
    """`day` itself if it is Monday–Friday, otherwise the Monday after."""
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def order_deadline(service_date):
    """Orders are handed in on the last working day before: on Friday for Saturday, Sunday and Monday."""
    return _at(previous_workday(service_date), settings.ORDER_DEADLINE)


def day_orders(task):
    return Task.objects.filter(type=ORDER_SUBMISSION, service_date=task.service_date)


def all_orders_resolved(service_date) -> bool:
    """Every active department has its order for the day, and all are approved or closed.

    Only the orders that exist would not do: one order opened early (days ahead, before the others'
    tasks exist) and approved would start the plan with one department of nine. Called under the
    lock_group lock, so two last approvals can't both miss each other."""
    from apps.orgstructure.models import Department

    statuses = dict(Task.objects.filter(type=ORDER_SUBMISSION, service_date=service_date)
                    .values_list("department_id", "status"))
    active = set(Department.objects.filter(is_active=True).values_list("pk", flat=True))
    return bool(active) and all(statuses.get(d) in CLOSED for d in active)


def _after_order_approved(task):
    from apps.orders.corrections import take_late_order

    if take_late_order(task):       # the plan has already left the dietician: in as «Пізнє замовлення»
        return
    open_plan_if_ready(task.service_date, parent=task)


def open_plan_if_ready(service_date, parent=None):
    """Open the day's menu planning once every active department's order is in and resolved. Not when there
    are no portions at all (every department «пацієнтів немає»): there is nothing to plan. Idempotent."""
    from apps.orders.services import approved_portions

    if Task.objects.filter(type=MENU_PLANNING, service_date=service_date).exists():
        return False
    if not (all_orders_resolved(service_date) and approved_portions(service_date)):
        return False
    from apps.planning.services import open_plan, sync_portions

    from .services import ensure_task

    ensure_task(MENU_PLANNING, service_date, parent=parent)
    sync_portions(open_plan(service_date))
    return True


def department_switched(department):
    """The set of active departments changed (Django admin, sync_departments): days still collecting orders
    follow. A department switched on gets its orders for the days whose deadline is still ahead; one switched
    off may have been the last the plan was waiting for."""
    from .services import ensure_task

    today = timezone.localdate()
    days = sorted(set(Task.objects.filter(type=ORDER_SUBMISSION, service_date__gte=today)
                      .values_list("service_date", flat=True)))
    now = timezone.now()
    for day in days:
        if department.is_active:
            if order_deadline(day) > now and not Task.objects.filter(type=MENU_PLANNING, service_date=day).exists():
                ensure_task(ORDER_SUBMISSION, day, department=department)
        else:
            open_plan_if_ready(day)


# Hooks into the apps that own each task's document. Imported lazily: workflow must not
# depend on them at import time.
def _validate_order(task):
    from apps.orders.services import validate_order_task

    return validate_order_task(task)


def _order_url(task):
    from django.urls import reverse

    return reverse("orders:edit", args=[task.department_id, task.service_date.isoformat()])


def _validate_plan(task):
    from apps.planning.services import validate_plan_task

    return validate_plan_task(task)


def _plan_url(task):
    from django.urls import reverse

    return reverse("planning:plan", args=[task.service_date.isoformat()])


def _after_plan_approved(task):
    from apps.planning.services import approve_plan

    approve_plan(task)          # freezes consumption (invariant 5) before cooking starts
    _next(COOKING)(task)


def _validate_cooking(task):
    from apps.consumption.services import validate_cooking_task

    return validate_cooking_task(task)


def _after_cooking_submitted(task):
    from apps.orders.corrections import close_for_kitchen_fact

    close_for_kitchen_fact(task.service_date)


def _kitchen_url(task):
    from django.urls import reverse

    return reverse("consumption:kitchen_day", args=[task.service_date.isoformat()])


def _validate_write_off(task):
    from apps.consumption.services import validate_write_off_task

    return validate_write_off_task(task)


def _write_off_url(task):
    from django.urls import reverse

    return reverse("consumption:write_off_day", args=[task.service_date.isoformat()])


def _after_write_off_approved(task):
    from apps.consumption.services import approve_write_off

    approve_write_off(task)


def _next(type_):
    def hook(task):
        from .services import ensure_task

        ensure_task(type_, task.service_date, parent=task)

    return hook


register(TaskDefinition(
    type=ORDER_SUBMISSION,
    title="Замовлення відділення",
    maker_role=Role.DEPT_NURSE,
    checker_role=Role.DIETICIAN,
    per_department=True,
    deadline=order_deadline,
    description="Вкажіть кількість порцій по дієтах на дату обслуговування.",
    validate_submit=_validate_order,
    subject_url=_order_url,
    action_label="Внести порції",
    on_approved=_after_order_approved,
    on_cancelled=_after_order_approved,  # cancelling the last open order also completes the day
    lock_group=day_orders,
    expires_with_day=True,
))

register(TaskDefinition(
    type=MENU_PLANNING,
    overdue_alerts=False,  # the hospital: no fixed hour for this stage
    title="План меню на день",
    maker_role=Role.DIETICIAN,
    checker_role=Role.CHEF,
    deadline=lambda d: _at(d - timedelta(days=1), "18:00"),
    description="Оберіть меню для кожної дієти з порціями.",
    validate_submit=_validate_plan,
    subject_url=_plan_url,
    action_label="Скласти план меню",
    on_approved=_after_plan_approved,
))

register(TaskDefinition(
    type=COOKING,
    overdue_alerts=False,  # the hospital: no fixed hour for this stage
    title="Приготування",
    maker_role=Role.CHEF,
    checker_role=Role.DIETICIAN,
    deadline=lambda d: _at(d, "20:00"),
    description="Внесіть фактичний розхід продуктів; відхилення понад 10% — з причиною.",
    validate_submit=_validate_cooking,
    subject_url=_kitchen_url,
    action_label="Внести факт",
    on_approved=_next(WRITE_OFF),
    on_submitted=_after_cooking_submitted,
))

register(TaskDefinition(
    type=WRITE_OFF,
    overdue_alerts=False,  # the hospital: no fixed hour for this stage
    title="Списання продуктів",
    maker_role=Role.ACCOUNTANT,
    checker_role=Role.DIETICIAN,
    deadline=lambda d: _at(d + timedelta(days=1), "12:00"),
    description="Списання продуктів за день: кількість з факту кухні, ціни з довідника.",
    validate_submit=_validate_write_off,
    subject_url=_write_off_url,
    action_label="Списати продукти",
    on_approved=_after_write_off_approved,
))


# ---------------------------------------------------------------- S7-01 order corrections
def _correction_url(task):
    from django.urls import reverse

    return reverse("orders:correction", args=[task.object_id]) if task.object_id else None


def _validate_correction(task):
    from apps.orders.corrections import validate_correction_task

    return validate_correction_task(task)


def _after_correction_approved(task):
    from apps.orders.corrections import apply_approved

    apply_approved(task)


register(TaskDefinition(
    type=ORDER_CORRECTION,
    title="Коригування замовлення",
    maker_role=Role.DEPT_NURSE,
    checker_role=Role.DIETICIAN,
    per_department=True,
    repeatable=True,
    deadline=lambda d: None,
    description="± порції за дієтами з обраного прийому їжі: надходження, виписка, переведення, зміна дієти.",
    validate_submit=_validate_correction,
    on_approved=_after_correction_approved,
    subject_url=_correction_url,
    action_label="Відкрити коригування",
    expires_with_day=True,
    overdue_alerts=False,
))
