from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
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
}


class MyTasksView(RoleRequiredMixin, View):
    allowed_roles = roles_for("tasks")

    def get(self, request):
        user = request.user
        querysets = {"todo": wf.todo_for(user), "review": wf.review_for(user), "history": wf.history_for(user)}
        tab = request.GET.get("tab")
        if tab not in querysets:
            tab = "review" if not querysets["todo"].exists() and querysets["review"].exists() else "todo"
        page = Paginator(querysets[tab], 50).get_page(request.GET.get("page"))
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
        return render(request, "workflow/task_detail.html", {
            "task": task,
            "definition": definition,
            "subject_url": definition.subject_url(task) if definition.subject_url else None,
            "events": task.events.select_related("actor"),
            "rejection": task.last_rejection if task.status == "rejected" else None,
            "allowed": {a: wf.can(request.user, task, a) for a in ACTIONS},
            "overdue": task.is_overdue(),
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
        return redirect("workflow:task_detail", pk=pk)


class AuditView(RoleRequiredMixin, View):
    allowed_roles = roles_for("audit")

    def get(self, request):
        qs = TaskEvent.objects.select_related("task__department", "actor").order_by("-created_at", "-id")
        kind = request.GET.get("kind", "")
        if kind:
            qs = qs.filter(kind=kind)
        page = Paginator(qs, 100).get_page(request.GET.get("page"))
        return render(request, "workflow/audit.html", {"page": page, "kinds": TaskEvent.Kind.choices, "kind": kind})


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
            return redirect("workflow:task_detail", pk=note.task_id)
        return redirect("workflow:my_tasks")


class NotificationReadAllView(RoleRequiredMixin, View):
    allowed_roles = roles_for("tasks")

    def post(self, request):
        Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
        if request.headers.get("HX-Request"):
            return NotificationListView().get(request)
        return redirect(request.POST.get("next") or "workflow:my_tasks")
