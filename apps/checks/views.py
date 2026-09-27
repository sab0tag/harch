from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.accounts.access import RoleRequiredMixin
from apps.core.navigation import roles_for
from apps.workflow import services as wf
from apps.workflow.models import TaskStatus

from . import services
from .models import CheckRun, Finding


class RunListView(RoleRequiredMixin, View):
    allowed_roles = roles_for("checks")

    def get(self, request):
        return render(request, "checks/run_list.html", {"runs": CheckRun.objects.select_related("task")[:50]})


class RunDetailView(RoleRequiredMixin, View):
    allowed_roles = roles_for("checks")

    def get(self, request, pk):
        run = get_object_or_404(CheckRun.objects.select_related("task"), pk=pk)
        findings = list(run.findings.select_related("resolved_by"))
        task = run.task
        return render(request, "checks/run_detail.html", {
            "run": run, "task": task, "findings": findings,
            "open_count": sum(f.status == Finding.Status.OPEN for f in findings),
            "editable": services.editable(run, request.user),
            "can_review": task is not None and wf.can(request.user, task, "approve"),
            "rejection": task.last_rejection if task and task.status == TaskStatus.REJECTED else None,
            "statuses": Finding.Status.choices,
        })

    def post(self, request, pk):
        run = get_object_or_404(CheckRun, pk=pk)
        try:
            if request.POST.get("action") == "submit":
                wf.submit(run.task, request.user, request.POST.get("comment", ""))
                messages.success(request, "Перевірку подано адміністратору.")
            else:
                finding = get_object_or_404(Finding, pk=request.POST.get("finding"), run=run)
                services.resolve(finding, request.user, request.POST.get("status", ""), request.POST.get("comment", ""))
        except ValidationError as exc:
            for msg in exc.messages:
                messages.error(request, msg)
        except (PermissionDenied, wf.InvalidTransition):
            messages.error(request, "Знахідки зараз не можна змінювати.")
        return redirect("checks:run", run.pk)
