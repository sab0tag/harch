"""S7-04: import of the 7-day menu cycle from Excel (apps/menus/cycle.py)."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views import View

from apps.accounts.access import RoleRequiredMixin
from apps.core.navigation import CATALOG_EDITORS, roles_for

from . import cycle
from .models import CYCLE_DAYS

SHORT_DAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")


class CycleEditorMixin(RoleRequiredMixin):
    allowed_roles = roles_for("menus")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_role(*CATALOG_EDITORS):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class CycleTemplateView(CycleEditorMixin, View):
    def get(self, request):
        return cycle.template_xlsx()


class CycleImportView(CycleEditorMixin, View):
    def get(self, request, errors=None):
        return render(request, "menus/cycle_import.html", {
            "status": cycle.cycle_status(), "days": list(zip((d for d, _ in CYCLE_DAYS), SHORT_DAYS)),
            "errors": errors or [],
        })

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            return self.get(request, ["Оберіть файл .xlsx."])
        if upload.size > 5 * 1024 * 1024:
            return self.get(request, ["Файл завеликий (до 5 МБ)."])
        rows, errors = cycle.parse(upload)
        if errors:
            return self.get(request, errors)
        request.session[cycle.SESSION_KEY] = {"rows": rows, "filename": upload.name[:200]}
        return redirect("menus:cycle_preview")


class CyclePreviewView(CycleEditorMixin, View):
    def stash(self, request):
        return request.session.get(cycle.SESSION_KEY)

    def get(self, request):
        stash = self.stash(request)
        if not stash:
            return redirect("menus:cycle_import")
        return render(request, "menus/cycle_preview.html", {
            "week": cycle.preview(stash["rows"]), "filename": stash["filename"], "rows": len(stash["rows"]),
        })

    def post(self, request):
        stash = self.stash(request)
        if not stash:
            return redirect("menus:cycle_import")
        rows, errors = stash["rows"], []
        if not rows:
            errors.append("Немає що застосувати.")
        if errors:
            return CycleImportView().get(request, errors)
        created = cycle.apply(rows, deactivate_others=request.POST.get("deactivate_others") == "on")
        request.session.pop(cycle.SESSION_KEY, None)
        messages.success(request, f"Створено меню 7-денного циклу: {len(created)}. План дня тепер підставляє їх за днем тижня.")
        return redirect("menus:cycle_import")
