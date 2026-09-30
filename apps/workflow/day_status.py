"""«Стан дня» for the admin: where the service days stand right now, to help where something is stuck.

Three cards around a day D (today by default): D+1 — department orders and the menu plan being prepared;
D — corrections and the kitchen; D−1 — the write-off. Read-only: each row links to its document.
"""
from datetime import timedelta

from django.urls import reverse

from apps.orgstructure.models import Department

from .definitions import COOKING, MENU_PLANNING, ORDER_CORRECTION, ORDER_SUBMISSION, WRITE_OFF
from .models import Task, TaskStatus


def _url(task):
    definition = task.definition
    return (definition.subject_url(task) if definition.subject_url else None) or \
        reverse("workflow:task_detail", args=[task.pk])


def _row(what, task, missing="", url=""):
    if task is None:
        return {"what": what, "status": None, "missing": missing, "url": url}
    rejection = task.last_rejection if task.status == TaskStatus.REJECTED else None
    return {
        "what": what, "status": task.status, "status_label": task.get_status_display(), "task": task,
        "who": task.resolved_by if task.status in (TaskStatus.APPROVED,) else task.submitted_by,
        "when": task.resolved_at if task.status == TaskStatus.APPROVED else task.submitted_at,
        "note": rejection.comment if rejection else "", "overdue": task.is_overdue(), "url": _url(task),
    }


def _one(type_, day):
    return Task.objects.filter(type=type_, service_date=day).select_related("submitted_by", "resolved_by").first()


def orders(day):
    tasks = {t.department_id: t for t in Task.objects.filter(type=ORDER_SUBMISSION, service_date=day)
             .select_related("submitted_by", "resolved_by")}
    rows = []
    for dept in Department.objects.filter(is_active=True).order_by("sort_order", "name"):
        rows.append(_row(dept.name, tasks.get(dept.pk), "задачу ще не створено",
                         reverse("orders:edit", args=[dept.pk, day.isoformat()])))
    return rows


def cards(anchor):
    tomorrow, yesterday = anchor + timedelta(days=1), anchor - timedelta(days=1)
    order_rows = orders(tomorrow)
    done = sum(r["status"] in (TaskStatus.APPROVED, TaskStatus.CANCELLED) for r in order_rows)
    submitted = sum(r["status"] == TaskStatus.SUBMITTED for r in order_rows)
    corrections = [_row(t.department.name if t.department else "—", t)
                   for t in Task.objects.filter(type=ORDER_CORRECTION, service_date=anchor)
                   .exclude(status=TaskStatus.CANCELLED).select_related("department", "submitted_by", "resolved_by")
                   .order_by("created_at")]
    return [
        {"key": "tomorrow", "day": tomorrow, "title": "Завтра — замовлення і план меню",
         "summary": f"Замовлень затверджено або закрито: {done} з {len(order_rows)}; на перевірці: {submitted}",
         "rows": order_rows + [_row("План меню", _one(MENU_PLANNING, tomorrow),
                                    "з'явиться, коли всі замовлення буде закрито")]},
        {"key": "today", "day": anchor, "title": "Сьогодні — коригування і кухня",
         "summary": f"Коригувань: {len(corrections)}",
         "rows": corrections + [_row("Факт кухні", _one(COOKING, anchor),
                                     "з'явиться після затвердження плану")]},
        {"key": "yesterday", "day": yesterday, "title": "Вчора — списання",
         "summary": "",
         "rows": [_row("Списання продуктів", _one(WRITE_OFF, yesterday), "з'явиться після затвердження факту кухні")]},
    ]
