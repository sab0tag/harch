"""The only place where task status changes (CLAUDE.md, «Workflow»).

Every transition: row lock (select_for_update) → permission check → status check →
TaskEvent → notifications → optional hook, all in one transaction.
"""
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Role

from . import notify as n
from .models import CLOSED, OPEN_FOR_MAKER, Task, TaskEvent, TaskStatus
from .registry import TaskDefinition, get


class InvalidTransition(Exception):
    pass


# ------------------------------------------------------------------ permissions
def _role_matches(user, role, task):
    if not (user.is_authenticated and user.is_active):
        return False
    if user.role == Role.ADMIN:
        return True
    if user.role != role:
        return False
    if role == Role.DEPT_NURSE:
        return task.department_id is not None and user.department_id == task.department_id
    return True


def is_maker(user, task):
    if task.maker_id and user.role != Role.ADMIN:
        return user.pk == task.maker_id
    return _role_matches(user, task.maker_role, task)


def is_checker(user, task):
    if task.checker_id and user.role != Role.ADMIN:
        return user.pk == task.checker_id
    return _role_matches(user, task.checker_role, task)


def can(user, task, action) -> bool:
    """Single permission check used by services, views and templates."""
    if action == "view":
        return (
            user.role == Role.ADMIN
            or is_maker(user, task)
            or is_checker(user, task)
            or task.events.filter(actor=user).exists()
        )
    if action in ("start", "submit"):
        return task.status in OPEN_FOR_MAKER and is_maker(user, task)
    if action in ("approve", "reject"):
        return (
            task.status == TaskStatus.SUBMITTED
            and is_checker(user, task)
            and user.pk != task.submitted_by_id  # maker ≠ checker
        )
    if action == "cancel":
        return user.role == Role.ADMIN and task.status in OPEN_FOR_MAKER
    return False


# ------------------------------------------------------------------ creation
def ensure_task(definition: TaskDefinition | str, service_date: date, *, department=None, parent=None, subject=None):
    """Create the task once; repeated calls (or races) return the existing one."""
    if isinstance(definition, str):
        definition = get(definition)
    if definition.per_department and department is None:
        raise ValueError(f"{definition.type} needs a department")
    lookup = {"type": definition.type, "service_date": service_date, "department": department}
    existing = Task.objects.filter(**lookup).first()
    if existing:
        return existing, False
    try:
        with transaction.atomic():
            task = Task.objects.create(
                **lookup,
                maker_role=definition.maker_role,
                checker_role=definition.checker_role,
                deadline=definition.deadline(service_date),
                parent=parent,
                subject=subject,
            )
            TaskEvent.objects.create(task=task, kind=TaskEvent.Kind.CREATED, to_status=task.status)
    except IntegrityError:
        return Task.objects.get(**lookup), False
    n.notify(n.makers(task), task, f"Нова задача: {task}", definition.description)
    return task, True


# ------------------------------------------------------------------ transitions
def _locked(task):
    group = task.definition.lock_group
    if group is not None:
        list(group(task).select_for_update().order_by("pk").values_list("pk", flat=True))
    return Task.objects.select_for_update().get(pk=task.pk)


def _event(task, kind, actor, from_status, comment=""):
    return TaskEvent.objects.create(
        task=task, kind=kind, actor=actor, from_status=from_status, to_status=task.status, comment=comment
    )


def _require(user, task, action):
    if not can(user, task, action):
        if task.status not in _ALLOWED_FROM[action]:
            raise InvalidTransition(
                f"Дію «{action}» не можна виконати для задачі у статусі «{task.get_status_display()}»"
            )
        raise PermissionDenied("Недостатньо прав для цієї дії")


_ALLOWED_FROM = {
    "start": OPEN_FOR_MAKER,
    "submit": OPEN_FOR_MAKER,
    "approve": (TaskStatus.SUBMITTED,),
    "reject": (TaskStatus.SUBMITTED,),
    "cancel": OPEN_FOR_MAKER,
}


def start(task, user):
    with transaction.atomic():
        task = _locked(task)
        _require(user, task, "start")
        if task.status != TaskStatus.PENDING:
            return task
        prev, task.status, task.started_at = task.status, TaskStatus.IN_PROGRESS, timezone.now()
        task.save(update_fields=["status", "started_at"])
        _event(task, TaskEvent.Kind.STARTED, user, prev)
    return task


def submit(task, user, comment=""):
    with transaction.atomic():
        task = _locked(task)
        _require(user, task, "submit")
        errors = task.definition.validate_submit(task)
        if errors:
            raise ValidationError(errors)
        now = timezone.now()
        if task.status == TaskStatus.PENDING:
            task.status, task.started_at = TaskStatus.IN_PROGRESS, now
            _event(task, TaskEvent.Kind.STARTED, user, TaskStatus.PENDING)
        prev = task.status
        task.status, task.submitted_at, task.submitted_by = TaskStatus.SUBMITTED, now, user
        task.save(update_fields=["status", "started_at", "submitted_at", "submitted_by"])
        _event(task, TaskEvent.Kind.SUBMITTED, user, prev, comment)
        again = " повторно" if prev == TaskStatus.REJECTED else ""
        n.notify(n.checkers(task), task, f"Подано{again} на перевірку: {task}", comment)
    return task


def approve(task, user, comment=""):
    with transaction.atomic():
        task = _locked(task)
        _require(user, task, "approve")
        prev = task.status
        task.status, task.resolved_at, task.resolved_by = TaskStatus.APPROVED, timezone.now(), user
        task.save(update_fields=["status", "resolved_at", "resolved_by"])
        _event(task, TaskEvent.Kind.APPROVED, user, prev, comment)
        n.notify([task.submitted_by], task, f"Затверджено: {task}", comment)
        task.definition.on_approved(task)
    return task


def reject(task, user, comment):
    if not (comment or "").strip():
        raise ValidationError("Вкажіть, що потрібно виправити — коментар обов'язковий.")
    with transaction.atomic():
        task = _locked(task)
        _require(user, task, "reject")
        prev = task.status
        task.status = TaskStatus.REJECTED
        task.reject_count += 1
        task.save(update_fields=["status", "reject_count"])
        _event(task, TaskEvent.Kind.REJECTED, user, prev, comment.strip())
        n.notify([task.submitted_by], task, f"Повернуто на доопрацювання: {task}", comment.strip())
    return task


def cancel(task, user, comment):
    if not (comment or "").strip():
        raise ValidationError("Вкажіть причину скасування.")
    with transaction.atomic():
        task = _locked(task)
        _require(user, task, "cancel")
        prev = task.status
        task.status, task.resolved_at, task.resolved_by = TaskStatus.CANCELLED, timezone.now(), user
        task.save(update_fields=["status", "resolved_at", "resolved_by"])
        _event(task, TaskEvent.Kind.CANCELLED, user, prev, comment.strip())
        n.notify(n.makers(task), task, f"Скасовано: {task}", comment.strip())
        task.definition.on_cancelled(task)
    return task


# ------------------------------------------------------------------ deadlines
def notify_overdue(now=None):
    """Notify once per overdue task: makers + all dieticians. Returns the tasks notified."""
    from django.contrib.auth import get_user_model

    now = now or timezone.now()
    notified = []
    for task in Task.objects.overdue(now).filter(overdue_notified_at__isnull=True).select_related("department"):
        with transaction.atomic():
            task = Task.objects.select_for_update().get(pk=task.pk)
            if task.overdue_notified_at or task.status in CLOSED:
                continue
            task.overdue_notified_at = now
            task.save(update_fields=["overdue_notified_at"])
            _event(task, TaskEvent.Kind.OVERDUE, None, task.status)
            recipients = {u.pk: u for u in n.makers(task)}
            recipients.update({u.pk: u for u in get_user_model().objects.filter(is_active=True, role=Role.DIETICIAN)})
            n.notify(recipients.values(), task, f"Прострочено: {task}")
            notified.append(task)
    return notified


# ------------------------------------------------------------------ inbox queries
def todo_for(user):
    """Tasks the user can work on as maker."""
    qs = Task.objects.open_for_maker().select_related("department")
    if user.role == Role.ADMIN:
        return qs
    qs = qs.filter(maker_role=user.role).filter(Q(maker__isnull=True) | Q(maker=user))
    if user.role == Role.DEPT_NURSE:
        qs = qs.filter(department_id=user.department_id)
    return qs


def review_for(user):
    """Submitted tasks the user may approve/reject (never their own submission)."""
    qs = Task.objects.filter(status=TaskStatus.SUBMITTED).exclude(submitted_by=user).select_related("department")
    if user.role == Role.ADMIN:
        return qs
    qs = qs.filter(checker_role=user.role).filter(Q(checker__isnull=True) | Q(checker=user))
    if user.role == Role.DEPT_NURSE:
        qs = qs.filter(department_id=user.department_id)
    return qs


def history_for(user):
    return Task.objects.filter(events__actor=user).distinct().select_related("department").order_by("-service_date", "-id")
