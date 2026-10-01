"""«Стан дня» for the admin: where the service days stand right now, to help where something is stuck.

Three cards around a day D (today by default): D+1 — department orders and the menu plan being prepared;
D — corrections and the kitchen; D−1 — the write-off. Read-only: each row links to its document.
"""
from datetime import timedelta

from django.urls import reverse

from apps.orgstructure.models import Department

from .definitions import COOKING, MENU_PLANNING, ORDER_CORRECTION, ORDER_SUBMISSION, WRITE_OFF, all_orders_resolved
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


def correction_rows(day):
    return [_row(f"Коригування · {t.department.name if t.department else '—'}", t)
            for t in Task.objects.filter(type=ORDER_CORRECTION, service_date=day)
            .exclude(status=TaskStatus.CANCELLED).select_related("department", "submitted_by", "resolved_by")
            .order_by("created_at")]


def store_row(day):
    from apps.consumption.models import StoreIssue
    from apps.planning.models import DailyPlan

    plan = DailyPlan.objects.filter(service_date=day, approved_at__isnull=False).first()
    url = reverse("consumption:store_day", args=[day.isoformat()])
    issue = StoreIssue.objects.filter(plan=plan, correction__isnull=True).select_related("issued_by").first() if plan else None
    if issue is None:
        return {"what": "Видача зі складу", "status": None, "url": url if plan else "",
                "missing": "ще не видано" if plan else "з'явиться після затвердження плану"}
    return {"what": "Видача зі складу", "status": "approved", "status_label": "Видано", "who": issue.issued_by,
            "when": issue.issued_at, "note": "", "overdue": False, "url": url}


def cards(anchor):
    tomorrow, yesterday = anchor + timedelta(days=1), anchor - timedelta(days=1)
    order_rows = orders(tomorrow)
    done = sum(r["status"] in (TaskStatus.APPROVED, TaskStatus.CANCELLED) for r in order_rows)
    submitted = sum(r["status"] == TaskStatus.SUBMITTED for r in order_rows)
    corrections = correction_rows(anchor)
    from apps.orders.services import approved_portions

    no_portions = all_orders_resolved(tomorrow) and not approved_portions(tomorrow, include_late=True)
    return [
        {"key": "tomorrow", "day": tomorrow, "title": "Завтра — замовлення і план меню",
         "summary": f"Замовлень затверджено або закрито: {done} з {len(order_rows)}; на перевірці: {submitted}",
         "rows": order_rows + correction_rows(tomorrow) + [_row("План меню", _one(MENU_PLANNING, tomorrow),
                                    "порцій немає — план не потрібен" if no_portions
                                    else "з'явиться, коли всі замовлення буде закрито")]},
        {"key": "today", "day": anchor, "title": "Сьогодні — коригування і кухня",
         "summary": f"Коригувань: {len(corrections)}",
         "rows": corrections + [store_row(anchor), _row("Факт кухні", _one(COOKING, anchor),
                                     "з'явиться після затвердження плану")]},
        {"key": "yesterday", "day": yesterday, "title": "Вчора — списання",
         "summary": "",
         "rows": [_row("Списання продуктів", _one(WRITE_OFF, yesterday), "з'явиться після затвердження факту кухні")]},
    ]
