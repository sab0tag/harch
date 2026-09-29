"""Day plan: portions from approved orders, one menu per diet (invariants 1, 2, 5)."""
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Diet
from apps.menus.models import Menu
from apps.orders.services import approved_portions
from apps.workflow import services as wf
from apps.workflow.definitions import MENU_PLANNING
from apps.workflow.models import OPEN_FOR_MAKER, Task

from .models import DailyPlan, DailyPlanEntry


def plan_task(service_date):
    return Task.objects.filter(type=MENU_PLANNING, service_date=service_date).first()


def open_plan(service_date):
    """Plan for the date, linked to its menu_planning task (if the task exists yet)."""
    task = plan_task(service_date)
    plan, _ = DailyPlan.objects.get_or_create(service_date=service_date, defaults={"task": task})
    if task and plan.task_id != task.pk:
        plan.task = task
        plan.save(update_fields=["task"])
    if task and task.object_id != plan.pk:
        task.subject = plan
        task.save(update_fields=["content_type", "object_id"])
    return plan


def is_editable_by(plan, user):
    return plan.task is not None and not plan.is_frozen and wf.can(user, plan.task, "submit")


def suggest_menu(diet, service_date):
    """Rotation: the menu after the one used most recently for this diet; else the first."""
    menus = list(Menu.objects.filter(diet=diet, is_active=True).order_by("number"))
    if not menus:
        return None
    last = (
        DailyPlanEntry.objects.filter(diet=diet, plan__service_date__lt=service_date, menu__isnull=False)
        .order_by("-plan__service_date")
        .values_list("menu__number", flat=True)
        .first()
    )
    if last is None:
        return menus[0]
    later = [m for m in menus if m.number > last]
    return later[0] if later else menus[0]


@transaction.atomic
def sync_portions(plan):
    """Portions follow approved orders only while the plan is with the dietician.

    Once submitted, the chef reviews exactly what was submitted; late approvals show up as a
    drift warning (see portions_drift) and the chef can return the plan to pick them up.
    """
    if plan.is_frozen or (plan.task is not None and plan.task.status not in OPEN_FOR_MAKER):
        return plan
    portions = approved_portions(plan.service_date)
    plan.entries.exclude(diet_id__in=portions).delete()
    existing = {e.diet_id: e for e in plan.entries.all()}
    for diet in Diet.objects.filter(pk__in=portions):
        entry = existing.get(diet.pk)
        if entry is None:
            DailyPlanEntry.objects.create(
                plan=plan, diet=diet, portions=portions[diet.pk], menu=suggest_menu(diet, plan.service_date)
            )
        elif entry.portions != portions[diet.pk]:
            entry.portions = portions[diet.pk]
            entry.save(update_fields=["portions"])
    return plan


@transaction.atomic
def set_menus(plan, user, menus_by_diet: dict):
    """menus_by_diet: {diet_id: menu_id or None}."""
    if not is_editable_by(plan, user):
        raise PermissionDenied("План зараз не можна редагувати.")
    errors = []
    for entry in plan.entries.select_related("diet"):
        if entry.diet_id not in menus_by_diet:
            continue
        menu_id = menus_by_diet[entry.diet_id]
        menu = Menu.objects.filter(pk=menu_id).first() if menu_id else None
        if menu and menu.diet_id != entry.diet_id:
            errors.append(f"Меню №{menu.number} не належить дієті {entry.diet.code}.")
            continue
        entry.menu = menu
        entry.save(update_fields=["menu"])
    if errors:
        raise ValidationError(errors)
    if plan.task.status == "pending":
        wf.start(plan.task, user)
    return plan


@transaction.atomic
def submit_plan(plan, user, menus_by_diet: dict, comment=""):
    sync_portions(plan)
    set_menus(plan, user, menus_by_diet)
    return wf.submit(plan.task, user, comment)


def validate_plan_task(task) -> list[str]:
    plan = DailyPlan.objects.filter(task=task).first()
    if plan is None:
        return ["План меню ще не сформовано."]
    sync_portions(plan)
    entries = list(plan.entries.select_related("diet", "menu"))
    if not entries:
        return ["Немає затверджених замовлень з порціями — планувати нічого."]
    errors = []
    for e in entries:
        if e.menu is None:
            errors.append(f"Оберіть меню для дієти {e.diet.code}.")
        elif e.menu.diet_id != e.diet_id:
            errors.append(f"Меню №{e.menu.number} не належить дієті {e.diet.code}.")
        elif not e.menu.is_active:
            errors.append(f"Меню №{e.menu.number} неактивне.")
    return errors


def approve_plan(task):
    """Registry hook: runs inside the approve transaction, before the cooking task is created."""
    from apps.consumption.services import freeze

    from apps.orders.corrections import apply_pending

    plan = DailyPlan.objects.select_for_update().get(task=task)
    freeze(plan)
    plan.approved_at = timezone.now()
    plan.save(update_fields=["approved_at"])
    apply_pending(plan)     # S7-01: corrections approved while the day was still being planned


def force_planning(service_date, user):
    """Dietician starts the plan before every order is resolved (e.g. after the deadline)."""
    if not user.has_role("dietician"):
        raise PermissionDenied
    task, _ = wf.ensure_task(MENU_PLANNING, service_date)
    plan = open_plan(service_date)
    sync_portions(plan)
    return plan


def portions_drift(plan):
    """{diet_code: (in_plan, approved)} where approved orders no longer match the plan."""
    approved = approved_portions(plan.service_date)
    in_plan = {e.diet_id: e.portions for e in plan.entries.all()}
    codes = dict(Diet.objects.filter(pk__in=set(approved) | set(in_plan)).values_list("pk", "code"))
    return {
        codes[d]: (in_plan.get(d, 0), approved.get(d, 0))
        for d in set(approved) | set(in_plan)
        if in_plan.get(d, 0) != approved.get(d, 0)
    }


def default_plan_date():
    return timezone.localdate() + timedelta(days=1)
