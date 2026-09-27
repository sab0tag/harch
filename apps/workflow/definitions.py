"""The four task types of a service day (docs/03_task_engine.md, «Типы задач v0.1»).

замовлення (кожне відділення) → план меню → приготування → списання

Subject objects (DailyOrder, DailyPlan, ...) and their submit validation arrive in Sprint 3;
the chain itself already works on tasks alone.
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


def order_deadline(service_date):
    return _at(service_date - timedelta(days=1), settings.ORDER_DEADLINE)


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
        from .services import ensure_task

        ensure_task(MENU_PLANNING, task.service_date, parent=task)


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
    on_approved=_after_order_approved,
    on_cancelled=_after_order_approved,  # cancelling the last open order also completes the day
    lock_group=day_orders,
))

register(TaskDefinition(
    type=MENU_PLANNING,
    title="План меню на день",
    maker_role=Role.DIETICIAN,
    checker_role=Role.CHEF,
    deadline=lambda d: _at(d - timedelta(days=1), "18:00"),
    description="Оберіть меню для кожної дієти з порціями.",
    on_approved=_next(COOKING),
))

register(TaskDefinition(
    type=COOKING,
    title="Приготування",
    maker_role=Role.CHEF,
    checker_role=Role.DIETICIAN,
    deadline=lambda d: _at(d, "20:00"),
    description="Відмітьте виконання та відхилення факту від плану.",
    on_approved=_next(WRITE_OFF),
))

register(TaskDefinition(
    type=WRITE_OFF,
    title="Списання продуктів",
    maker_role=Role.ACCOUNTANT,
    checker_role=Role.DIETICIAN,
    deadline=lambda d: _at(d + timedelta(days=1), "12:00"),
    description="Списання продуктів за день на підставі затвердженого плану.",
))
