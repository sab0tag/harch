from datetime import date

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View

from apps.accounts.access import RoleRequiredMixin
from apps.core.navigation import roles_for
from apps.orders.views import parse_date
from apps.planning.models import DailyPlan

from .exports import accumulative_data, accumulative_xlsx, menu_vymoha_xlsx


class ReportsIndexView(RoleRequiredMixin, View):
    allowed_roles = roles_for("reports")

    def get(self, request):
        today = timezone.localdate()
        month = request.GET.get("month") or today.strftime("%Y-%m")
        return render(request, "reports/index.html", {"today": today, "month": month})


class MenuPdfView(RoleRequiredMixin, View):
    """Меню-розкладка for printing and signatures. Chef prints it too, so plan roles apply."""

    allowed_roles = roles_for("plan")

    def get(self, request, day):
        plan = DailyPlan.objects.filter(service_date=parse_date(day)).first()
        if plan is None or not plan.is_frozen:
            messages.error(request, "Меню-розкладку можна надрукувати лише для затвердженого плану.")
            return redirect("planning:plan", parse_date(day).isoformat())
        from .pdf import menu_rozkladka_pdf

        return menu_rozkladka_pdf(plan)


class BrakerazhPdfView(RoleRequiredMixin, View):
    """S7-03: journal of finished-dish quality control — printed in the kitchen, so kitchen roles."""

    allowed_roles = roles_for("kitchen")

    def get(self, request, day):
        plan = DailyPlan.objects.filter(service_date=parse_date(day)).first()
        if plan is None or not plan.is_frozen:
            messages.error(request, "Бракеражний журнал формується лише для затвердженого плану.")
            return redirect("consumption:kitchen_day", parse_date(day).isoformat())
        from .pdf import brakerazh_pdf

        return brakerazh_pdf(plan)


class MenuVymohaView(RoleRequiredMixin, View):
    """Меню-вимога (typical form З-4а) for a day, XLSX. Accounting document: reports roles."""

    allowed_roles = roles_for("reports")

    def get(self, request, day):
        plan = DailyPlan.objects.filter(service_date=parse_date(day)).first()
        if plan is None or not plan.is_frozen:
            messages.error(request, "Меню-вимога формується лише для затвердженого плану.")
            return redirect("reports:index")
        return menu_vymoha_xlsx(plan)


def parse_month(value):
    try:
        year, month = (int(x) for x in value.split("-"))
        return date(year, month, 1)
    except (ValueError, AttributeError) as exc:
        raise Http404 from exc


class AccumulativeView(RoleRequiredMixin, View):
    allowed_roles = roles_for("reports")

    def get(self, request, month):
        first = parse_month(month)
        if request.GET.get("format") == "xlsx":
            return accumulative_xlsx(first.year, first.month)
        data = accumulative_data(first.year, first.month)
        return render(request, "reports/accumulative.html", {
            "first": first, "data": data, "day_range": range(1, data["days"] + 1)})
