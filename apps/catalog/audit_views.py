from collections import OrderedDict

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from apps.accounts.access import RoleRequiredMixin
from apps.core.navigation import roles_for

from . import audit
from .audit import KINDS, object_url, run_audit
from .models import ReferenceFinding

SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


class AuditView(RoleRequiredMixin, View):
    allowed_roles = roles_for("audit_ref")

    def get(self, request):
        status = request.GET.get("status", "open")
        if status not in dict(ReferenceFinding.Status.choices):
            status = "open"
        qs = ReferenceFinding.objects.filter(status=status).select_related("dismissed_by")
        groups = OrderedDict()
        for f in sorted(qs, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.kind, f.title)):
            f.url = object_url(f)
            groups.setdefault(f.kind, {"label": KINDS.get(f.kind, f.kind), "severity": f.severity, "items": []})["items"].append(f)
        counts = {s: ReferenceFinding.objects.filter(status=s).count() for s, _ in ReferenceFinding.Status.choices}
        return render(request, "catalog/audit.html", {
            "groups": list(groups.values()), "status": status, "counts": counts,
            "statuses": ReferenceFinding.Status.choices,
        })

    def post(self, request):
        if request.user.has_role("dietician") or request.user.role == "admin":
            counts = run_audit(with_ai=request.POST.get("ai") == "1", user=request.user)
            msg = f"Перевірено. Нових: {counts['new']}, виправлено: {counts['resolved']}, відкритих: {counts['open']}."
            messages.success(request, msg)
            if counts["ai"] and counts["ai"] != "ok":
                messages.warning(request, counts["ai"])
        return redirect("catalog:audit")


class FindingDismissView(RoleRequiredMixin, View):
    allowed_roles = roles_for("audit_ref")

    def post(self, request, pk):
        f = get_object_or_404(ReferenceFinding, pk=pk)
        if request.POST.get("action") == "reopen":
            audit.reopen(f)
            messages.success(request, "Зауваження знову відкрите.")
        else:
            try:
                audit.dismiss(f, request.user, request.POST.get("note", ""))
                messages.success(request, f"«{f.title}»: позначено як «не проблема».")
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
        back = request.POST.get("back", "")
        if back and url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()},
                                                    require_https=request.is_secure()):
            return redirect(back)
        return redirect("catalog:audit")
