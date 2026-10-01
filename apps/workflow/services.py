"""The only place where task status changes (CLAUDE.md, «Workflow»).

Every transition: row lock (select_for_update) → permission check → status check →
TaskEvent → notifications → optional hook, all in one transaction.
"""
from datetime import date, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.conf import settings
from django.utils import timezone

from apps.accounts.models import Role

from . import notify as n
from .models import CLOSED, OPEN_FOR_MAKER, Task, TaskEvent, TaskStatus
from .registry import TaskDefinition, all_definitions, get


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
    """Approving and returning is the checker role's job only (the hospital: the dietician, the chef for
    the plan). An admin sees everything, cancels and catches up missed days, but does not approve."""
    if user.role == Role.ADMIN:
        return settings.ADMIN_CAN_APPROVE       # the pilot safety net, marked in the history
    if task.checker_id:
        return user.pk == task.checker_id
    return _role_matches(user, task.checker_role, task)


def day_is_over(task, today=None) -> bool:
    """An expires_with_day task (a department order) whose service date has already passed."""
    return task.definition.expires_with_day and task.service_date < (today or timezone.localdate())


def _expiring_types():
    return [d.type for d in all_definitions() if d.expires_with_day]


# Day-wide tasks whose document has its own screen: whoever may open the screen may read the task's
# history too (the accountant the plan's, the director the write-off's). Department tasks stay per department.
DOCUMENT_SCREEN = {"menu_planning": "plan", "cooking": "kitchen", "write_off": "write-off"}


def _sees_document(user, task):
    from apps.core.navigation import roles_for

    screen = DOCUMENT_SCREEN.get(task.type)
    return screen is not None and user.role in roles_for(screen)


def can(user, task, action) -> bool:
    """Single permission check used by services, views and templates."""
    if action == "view":
        return (
            user.role == Role.ADMIN
            or is_maker(user, task)
            or is_checker(user, task)
            or _sees_document(user, task)
            or task.events.filter(actor=user).exists()
        )
    if action in ("start", "submit"):
        # A past day's order is no longer the nurse's to fill in; an admin may still catch up a missed day.
        return (task.status in OPEN_FOR_MAKER and is_maker(user, task)
                and (user.role == Role.ADMIN or not day_is_over(task)))
    if action in ("approve", "reject"):
        return (
            task.status == TaskStatus.SUBMITTED
            and is_checker(user, task)
            and user.pk != task.submitted_by_id  # maker ≠ checker
        )
    if action == "cancel":
        return user.role == Role.ADMIN and task.status in OPEN_FOR_MAKER
    if action == "reopen":
        # A past day's order closed (at night, or by hand): the admin may open it again to catch the day up.
        return user.role == Role.ADMIN and task.status == TaskStatus.CANCELLED and day_is_over(task)
    return False


# ------------------------------------------------------------------ creation
def ensure_task(definition: TaskDefinition | str, service_date: date, *, department=None, parent=None, subject=None):
    """Create the task once; repeated calls (or races) return the existing one."""
    if isinstance(definition, str):
        definition = get(definition)
    if definition.per_department and department is None:
        raise ValueError(f"{definition.type} needs a department")
    if definition.repeatable:
        raise ValueError(f"{definition.type} may repeat within a day: use create_task")
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


def create_task(definition: TaskDefinition | str, service_date: date, *, department=None, subject=None):
    """A new task every time — for repeatable types (a department may correct its order several times a day).
    The maker creates it for herself, so nobody is notified until it is submitted."""
    if isinstance(definition, str):
        definition = get(definition)
    if not definition.repeatable:
        raise ValueError(f"{definition.type} is created once per day: use ensure_task")
    if definition.per_department and department is None:
        raise ValueError(f"{definition.type} needs a department")
    task = Task.objects.create(
        type=definition.type, service_date=service_date, department=department,
        maker_role=definition.maker_role, checker_role=definition.checker_role,
        deadline=definition.deadline(service_date), subject=subject,
    )
    TaskEvent.objects.create(task=task, kind=TaskEvent.Kind.CREATED, to_status=task.status)
    return task


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
    "reopen": (TaskStatus.CANCELLED,),
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
        task.definition.on_submitted(task)
    return task


def approve(task, user, comment=""):
    with transaction.atomic():
        task = _locked(task)
        _require(user, task, "approve")
        prev = task.status
        task.status, task.resolved_at, task.resolved_by = TaskStatus.APPROVED, timezone.now(), user
        task.save(update_fields=["status", "resolved_at", "resolved_by"])
        comment = _admin_mark(user, "Затверджено", _substitution_mark(task, user, comment))
        _event(task, TaskEvent.Kind.APPROVED, user, prev, comment)
        n.notify([task.submitted_by], task, f"Затверджено: {task}", comment)
        task.definition.on_approved(task)
    return task


def _admin_mark(user, verb, comment):
    """ADMIN_CAN_APPROVE: an admin's decision says so in the history."""
    if user.role != Role.ADMIN:
        return comment
    return f"{verb} адміністратором (підстраховка). {comment}".strip()


def _substitution_mark(task, user, comment):
    """S7-06: approving what the same person submitted under their other login (substitution) is
    allowed — the logins differ — but it is written into the history and the admins are told."""
    submitter = task.submitted_by
    if submitter is None or submitter.pk == user.pk or not user.is_same_person(submitter):
        return comment
    from django.contrib.auth import get_user_model

    mark = f"Заміщення: затвердила та сама особа, що й подала (вхід «{submitter.username}»)."
    admins = get_user_model().objects.filter(role=Role.ADMIN, is_active=True)
    n.notify(admins, task, f"Заміщення: {task} затверджено тією ж особою", mark)
    return f"{mark} {comment}".strip()


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
        _event(task, TaskEvent.Kind.REJECTED, user, prev, _admin_mark(user, "Повернуто", comment.strip()))
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


def reopen(task, user, comment=""):
    """Admin catch-up: a closed past day's order is open again (pending) for the admin to fill in on the screen.
    Nobody is notified: it is not back in the nurse's list (see todo_for); the history says who reopened it."""
    with transaction.atomic():
        task = _locked(task)
        _require(user, task, "reopen")
        prev = task.status
        task.status, task.resolved_at, task.resolved_by = TaskStatus.PENDING, None, None
        task.save(update_fields=["status", "resolved_at", "resolved_by"])
        _event(task, TaskEvent.Kind.REOPENED, user, prev, comment.strip() or "Відкрито заново для заповнення заднім числом")
    return task


# ------------------------------------------------------------------ discussion on a document
COMMENT_MAX = 1000
# Roles that may be called into a document's discussion (its own maker/checker are there anyway).
CALLABLE_ROLES = (Role.DIETICIAN, Role.CHEF, Role.STOREKEEPER, Role.ACCOUNTANT, Role.DIRECTOR)


def addressee_labels(event):
    return [Role(r).label for r in event.addressed_to.split(",") if r in Role.values]


def discussion(task):
    return task.events.filter(kind=TaskEvent.Kind.COMMENT).select_related("actor").order_by("created_at")


def comment(task, user, text, call_roles=()):
    """A remark on the document between its people — no status change. Told: its maker and checker side,
    everyone who already acted on it, and any roles called in; never the author."""
    text = (text or "").strip()
    if not text:
        raise ValidationError("Напишіть коментар.")
    if len(text) > COMMENT_MAX:
        raise ValidationError(f"Коментар задовгий: до {COMMENT_MAX} символів.")
    if not can(user, task, "view"):
        raise PermissionDenied("Недостатньо прав для цієї дії")
    with transaction.atomic():
        called = [r for r in dict.fromkeys(call_roles) if r in CALLABLE_ROLES]
        event = TaskEvent.objects.create(task=task, kind=TaskEvent.Kind.COMMENT, actor=user,
                                         from_status=task.status, to_status=task.status, comment=text,
                                         addressed_to=",".join(called))
        from django.contrib.auth import get_user_model

        people = {u.pk: u for u in n.makers(task)}
        people.update({u.pk: u for u in n.checkers(task)})
        actors = task.events.exclude(actor__isnull=True).values_list("actor", flat=True)
        people.update({u.pk: u for u in get_user_model().objects.filter(pk__in=actors, is_active=True)})
        people.update({u.pk: u for u in get_user_model().objects.filter(role__in=called, is_active=True)})
        people.pop(user.pk, None)
        to = f" → {', '.join(addressee_labels(event))}" if called else ""
        n.notify(people.values(), task, f"Коментар: {task}", f"{user.get_full_name() or user}{to}: {text[:300]}")
    return event


# ------------------------------------------------------------------ deadlines
def notify_overdue(now=None):
    """Notify once per overdue task: makers + all dieticians. Returns the tasks notified.

    Only deadlines missed within the last 24 hours: a past day an admin opens to catch up must not
    bring a wave of «Прострочено» (and after a long outage, old news helps nobody)."""
    from django.contrib.auth import get_user_model

    now = now or timezone.now()
    notified = []
    fresh = Task.objects.overdue(now).filter(overdue_notified_at__isnull=True, deadline__gte=now - timedelta(days=1))
    for task in fresh.select_related("department"):
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


def close_by_system(task, comment, *, run_hook=False, also_submitted=False):
    """Close an open task without a person: cancelled, with the reason in its history, no notifications.
    run_hook: also run the type's on_cancelled (e.g. the day's last open order closing starts the plan).
    also_submitted: close one still waiting for review too (a past day's order nobody approved)."""
    closable = (*OPEN_FOR_MAKER, TaskStatus.SUBMITTED) if also_submitted else OPEN_FOR_MAKER
    with transaction.atomic():
        task = _locked(task)
        if task.status not in closable:
            return task
        prev = task.status
        task.status = TaskStatus.CANCELLED
        task.resolved_at = timezone.now()
        task.save(update_fields=["status", "resolved_at"])
        _event(task, TaskEvent.Kind.CANCELLED, None, prev, comment)
        if run_hook:
            task.definition.on_cancelled(task)
    return task


STALE_PLAN_DAYS = 3


def expire_stale_tasks(today=None, dry_run=False):
    """Close what is left open of past days' expires_with_day tasks (nightly, deploy/crontab).

    Submitted ones too: approving yesterday's order the next morning would open a plan for a day already
    cooked. No hook: a finished day must not start its menu planning now. Returns the tasks (to be) closed.
    An admin catching up a past day reopens its order (reopen) and then has STALE_PLAN_DAYS to finish it."""
    today = today or timezone.localdate()
    open_ = Q(status__in=OPEN_FOR_MAKER) | Q(status=TaskStatus.SUBMITTED)
    # A day the admin reopened to catch up gets STALE_PLAN_DAYS to be finished.
    quiet_since = timezone.now() - timedelta(days=STALE_PLAN_DAYS)
    stale = list(Task.objects.filter(open_, type__in=_expiring_types(), service_date__lt=today)
                 .exclude(pk__in=TaskEvent.objects.filter(kind=TaskEvent.Kind.REOPENED, created_at__gte=quiet_since)
                          .values("task_id"))
                 .select_related("department").order_by("service_date", "department__name"))
    # Menu plans of past days nobody has touched for STALE_PLAN_DAYS: the day was cooked without the system.
    # (An admin catching up a past day keeps its plan alive by working on it.)
    from django.db.models import Max

    from .definitions import MENU_PLANNING

    stale += list(Task.objects.filter(open_, type=MENU_PLANNING, service_date__lt=today)
                  .annotate(last=Max("events__created_at")).filter(last__lt=quiet_since).order_by("service_date"))
    if not dry_run:
        for task in stale:
            if task.type == MENU_PLANNING:
                close_by_system(task, f"День минув, план не чіпали {STALE_PLAN_DAYS} дні — закрито автоматично",
                                also_submitted=True)
                continue
            reason = ("Дата минула, не розглянуто — закрито автоматично" if task.status == TaskStatus.SUBMITTED
                      else "Дата минула — закрито автоматично")
            close_by_system(task, reason, also_submitted=True)
    return stale


# ------------------------------------------------------------------ inbox queries
def todo_for(user):
    """Tasks the user can work on as maker. The admin has no daily work of their own: nothing is queued
    for them (they can still step in on the screens, e.g. to catch up a missed day)."""
    qs = Task.objects.open_for_maker().select_related("department")
    if user.role == Role.ADMIN:
        return qs.none()
    qs = qs.filter(maker_role=user.role).filter(Q(maker__isnull=True) | Q(maker=user))
    qs = qs.exclude(type__in=_expiring_types(), service_date__lt=timezone.localdate())  # see day_is_over
    if user.role == Role.DEPT_NURSE:
        qs = qs.filter(department_id=user.department_id)
    return qs


def review_for(user):
    """Submitted tasks the user may approve/reject (never their own submission)."""
    qs = Task.objects.filter(status=TaskStatus.SUBMITTED).exclude(submitted_by=user).select_related("department")
    if user.role == Role.ADMIN:
        return qs if settings.ADMIN_CAN_APPROVE else qs.none()     # see is_checker
    qs = qs.filter(checker_role=user.role).filter(Q(checker__isnull=True) | Q(checker=user))
    if user.role == Role.DEPT_NURSE:
        qs = qs.filter(department_id=user.department_id)
    return qs


def history_for(user):
    return Task.objects.filter(events__actor=user).distinct().select_related("department").order_by("-service_date", "-id")
