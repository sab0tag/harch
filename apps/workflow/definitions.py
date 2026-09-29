"""The four task types of a service day (docs/03_task_engine.md, «Типы задач v0.1»).

замовлення (кожне відділення) → план меню → приготування → списання

Subjects: order → DailyOrder, menu_planning and cooking → DailyPlan, write_off → WriteOff.
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
    """Called under the lock_group lock, so two last approvals can't both miss each other."""
    statuses = list(
        Task.objects.filter(type=ORDER_SUBMISSION, service_date=service_date).values_list("status", flat=True)
    )
    return bool(statuses) and all(s in CLOSED for s in statuses)


def _after_order_approved(task):
    if all_orders_resolved(task.service_date):
        from apps.planning.services import open_plan, sync_portions

        from .services import ensure_task

        ensure_task(MENU_PLANNING, task.service_date, parent=task)
        sync_portions(open_plan(task.service_date))


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
