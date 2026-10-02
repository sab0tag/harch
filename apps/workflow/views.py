from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import F
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from apps.accounts.access import RoleRequiredMixin
from apps.core.navigation import roles_for

from . import services as wf
from .models import Notification, Task, TaskEvent

TABS = (
    ("todo", "Мені виконати"),
    ("review", "Мені перевірити"),
    ("history", "Історія"),
)
ACTIONS = {
    "start": (wf.start, "Задачу взято в роботу."),
    "submit": (wf.submit, "Подано на перевірку."),
    "approve": (wf.approve, "Затверджено."),
    "reject": (wf.reject, "Повернуто на доопрацювання."),
    "cancel": (wf.cancel, "Задачу скасовано."),
    "recall": (wf.recall, "Відкликано — документ знову можна редагувати."),
}


def correction_hint(task, user, allowed):
    """Where a correction goes at this point of the task's life (invariant 8)."""
    from apps.accounts.models import Role

    checker = dict(Role.choices).get(task.checker_role, task.checker_role).lower()
    if task.status in ("pending", "in_progress", "rejected"):
        if allowed["submit"]:
            return "Поки задача у вас, усі зміни вносяться в документі. Після подачі редагування закривається."
        return "Документ зараз заповнює виконавець — зміни вносить він до подачі."
    if task.status == "submitted":
        if allowed["approve"]:
            return "Якщо потрібні виправлення — натисніть «Повернути» і напишіть, що змінити: документ знову стане редагованим у виконавця."
        return f"Подано — редагування закрите. Потрібно виправити? Попросіть, щоб {checker} повернув(ла) задачу з коментарем — вона знову стане редагованою у вас."
    if task.status == "approved":
        return "Затверджено — документ заблоковано назавжди, щоб цифри в розході й списанні не змінювались заднім числом."
    return ""


class MyTasksView(RoleRequiredMixin, View):
    allowed_roles = roles_for("tasks")

    def get(self, request):
        user = request.user
        querysets = {
            # Most urgent first: overdue and nearest deadlines on top.
            "todo": wf.todo_for(user).order_by(F("deadline").asc(nulls_last=True), "service_date", "id"),
            # The checker has no deadline of their own: whoever has waited longest comes first.
            "review": wf.review_for(user).select_related("submitted_by").order_by(F("submitted_at").asc(nulls_last=True), "id"),
            "history": wf.history_for(user),
        }
        tab = request.GET.get("tab")
        if tab not in querysets:
            tab = "review" if not querysets["todo"].exists() and querysets["review"].exists() else "todo"
        page = Paginator(querysets[tab], 50).get_page(request.GET.get("page"))
        for task in page:
            # Makers go straight to the document they fill in; the task card is one click further.
            definition = task.definition
            task.doc_url = definition.subject_url(task) if definition.subject_url else None
            task.doc_label = definition.action_label if tab == "todo" else "Переглянути"
        return render(request, "workflow/my_tasks.html", {
            "tabs": [(k, label, querysets[k].count() if k != "history" else None) for k, label in TABS],
            "tab": tab,
            "page": page,
            "now": timezone.now(),
        })


class TaskDetailView(RoleRequiredMixin, View):
    allowed_roles = roles_for("tasks")

    def get(self, request, pk):
        task = get_object_or_404(Task.objects.select_related("department", "submitted_by", "resolved_by"), pk=pk)
        if not wf.can(request.user, task, "view"):
            raise Http404  # don't reveal that another department's task exists
        definition = task.definition
        allowed = {a: wf.can(request.user, task, a) for a in ACTIONS}
        return render(request, "workflow/task_detail.html", {
            "task": task,
            "definition": definition,
            "subject_url": definition.subject_url(task) if definition.subject_url else None,
            "correction_hint": correction_hint(task, request.user, allowed),
            "events": task.events.select_related("actor"),
            "rejection": task.last_rejection if task.status == "rejected" else None,
            "allowed": allowed,
            "overdue": task.is_overdue(),
            "parent_visible": task.parent_id is not None and wf.can(request.user, task.parent, "view"),
        })


class TaskActionView(RoleRequiredMixin, View):
    allowed_roles = roles_for("tasks")

    def post(self, request, pk, action):
        if action not in ACTIONS:
            raise Http404
        task = get_object_or_404(Task, pk=pk)
        if not wf.can(request.user, task, "view"):
            raise Http404
        fn, ok_message = ACTIONS[action]
        comment = request.POST.get("comment", "")
        try:
            fn(task, request.user) if action == "start" else fn(task, request.user, comment)
        except ValidationError as exc:
            for msg in exc.messages:
                messages.error(request, msg)
        except (PermissionDenied, wf.InvalidTransition) as exc:
            messages.error(request, str(exc) or "Ця дія зараз недоступна.")
        else:
            messages.success(request, ok_message)
        nxt = request.POST.get("next", "")
        if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
            return redirect(nxt)
        return redirect("workflow:task_detail", pk=pk)


class TaskCommentView(RoleRequiredMixin, View):
    """«Обговорення» under a document: a remark to its people, kept in the task's history."""

    allowed_roles = roles_for("tasks")

    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk)
        if not wf.can(request.user, task, "view"):
            raise Http404
        try:
            wf.comment(task, request.user, request.POST.get("text", ""), request.POST.getlist("call"))
            messages.success(request, "Коментар надіслано.")
        except ValidationError as exc:
            for msg in exc.messages:
                messages.error(request, msg)
        nxt = request.POST.get("next", "")
        if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
            return redirect(nxt + "#discussion")
        return redirect("workflow:task_detail", pk=pk)


class DayStatusView(RoleRequiredMixin, View):
    """«Стан дня»: where tomorrow's orders, today's kitchen and yesterday's write-off stand (admin)."""

    allowed_roles = roles_for("day")

    def get(self, request):
        from datetime import date as date_cls, timedelta

        from .day_status import cards

        try:
            anchor = date_cls.fromisoformat(request.GET.get("date", ""))
        except ValueError:
            anchor = timezone.localdate()
        return render(request, "workflow/day_status.html", {
            "anchor": anchor, "cards": cards(anchor), "today": timezone.localdate(),
            "prev_day": anchor - timedelta(days=1), "next_day": anchor + timedelta(days=1),
        })


class AuditView(RoleRequiredMixin, View):
    allowed_roles = roles_for("audit")

    def get(self, request):
        if request.GET.get("tab") == "signins":
            from apps.accounts import signins

            flagged, who = request.GET.get("flagged") == "1", request.GET.get("who", "").strip()
            return render(request, "workflow/audit_signins.html", {
                "rows": signins.rows(only_flagged=flagged, username=who), "flagged": flagged, "who": who,
                "networks_set": bool(signins.networks()),
            })
        qs = TaskEvent.objects.select_related("task__department", "actor").order_by("-created_at", "-id")
        kind = request.GET.get("kind", "")
        if kind:
            qs = qs.filter(kind=kind)
        by = request.GET.get("by", "")
        if by == "system":
            qs = qs.filter(actor__isnull=True)
        elif by == "people":
            qs = qs.filter(actor__isnull=False, seeded=False)
        page = Paginator(qs, 100).get_page(request.GET.get("page"))
        return render(request, "workflow/audit.html", {"page": page, "kinds": TaskEvent.Kind.choices, "kind": kind,
                                                       "by": by})


# ------------------------------------------------------------- notifications
class NotificationCountView(RoleRequiredMixin, View):
    allowed_roles = roles_for("tasks")

    def get(self, request):
        count = Notification.objects.filter(user=request.user, read_at__isnull=True).count()
        return render(request, "workflow/_notification_count.html", {"count": count})


class NotificationListView(RoleRequiredMixin, View):
    allowed_roles = roles_for("tasks")

    def get(self, request):
        items = Notification.objects.filter(user=request.user).select_related("task")[:10]
        unread = Notification.objects.filter(user=request.user, read_at__isnull=True).count()
        return render(request, "workflow/_notification_list.html", {"items": items, "unread": unread})


class NotificationOpenView(RoleRequiredMixin, View):
    allowed_roles = roles_for("tasks")

    def get(self, request, pk):
        note = get_object_or_404(Notification, pk=pk, user=request.user)
        if note.read_at is None:
            note.read_at = timezone.now()
            note.save(update_fields=["read_at"])
        if note.task_id:
            # Straight to the document where the work (or the review) happens; the card is one click further.
            task = note.task
            definition = task.definition
            url = definition.subject_url(task) if definition.subject_url else None
            return redirect(url or reverse("workflow:task_detail", kwargs={"pk": task.pk}))
        if note.url and url_has_allowed_host_and_scheme(note.url, allowed_hosts={request.get_host()}):
            return redirect(note.url)
        return redirect("workflow:my_tasks")


class NotificationReadAllView(RoleRequiredMixin, View):
    allowed_roles = roles_for("tasks")

    def post(self, request):
        Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
        if request.headers.get("HX-Request"):
            return NotificationListView().get(request)
        return redirect(request.POST.get("next") or "workflow:my_tasks")
