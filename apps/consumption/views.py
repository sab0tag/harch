from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View

from apps.accounts.access import RoleRequiredMixin
from apps.catalog.models import Product
from apps.core.navigation import PRICE_EDITORS, roles_for
from apps.orders.views import parse_date
from apps.planning.models import DailyPlan
from apps.workflow import services as wf
from apps.workflow.models import TaskStatus

from . import services
from .models import ActualConsumption


def day_nav(day):
    return {"prev_day": day - timedelta(days=1), "next_day": day + timedelta(days=1)}


def frozen_plan(day):
    plan = DailyPlan.objects.filter(service_date=day).select_related("task").first()
    return plan if plan and plan.is_frozen else None


# ====================================================================== S4-01 kitchen
class KitchenView(RoleRequiredMixin, View):
    allowed_roles = roles_for("kitchen")

    def get(self, request, day=None):
        day = parse_date(day, default=timezone.localdate())
        plan = frozen_plan(day)
        if plan is None:
            return render(request, "consumption/kitchen_empty.html", {"day": day, **day_nav(day)})
        task = services.cooking_task(plan)
        if task is not None and task.object_id is None:
            services.link_subject(task, plan)
        editable = services.kitchen_editable(plan, request.user)
        services.ensure_actuals(plan)   # fact table starts equal to the frozen plan
        return self.render(request, plan, task, editable)

    def post(self, request, day=None):
        day = parse_date(day, default=timezone.localdate())
        plan = frozen_plan(day)
        if plan is None:
            raise Http404
        values, errors = {}, []
        for a in ActualConsumption.objects.filter(plan=plan):
            try:
                grams = services.parse_kg(request.POST.get(f"fact_{a.pk}"), a.product_name)
            except ValidationError as exc:
                errors.extend(exc.messages)
                continue
            values[a.pk] = (grams, request.POST.get(f"reason_{a.pk}", ""))
        extra = None
        extra_id = request.POST.get("extra_product")
        if extra_id and extra_id.isdigit():
            product = Product.objects.filter(pk=extra_id, is_active=True).first()
            try:
                grams = services.parse_kg(request.POST.get("extra_kg"), "Заміна")
            except ValidationError as exc:
                errors.extend(exc.messages)
                grams = None
            if product and grams:
                extra = (product, grams, request.POST.get("extra_reason", "") or "заміна")
            elif product:
                errors.append("Для доданого продукту вкажіть кількість у кг.")
        if errors:
            for e in errors:
                messages.error(request, e)
            return redirect("consumption:kitchen_day", day.isoformat())
        try:
            services.save_actuals(plan, request.user, values, extra)
            if request.POST.get("action") == "submit":
                wf.submit(services.cooking_task(plan), request.user, request.POST.get("comment", ""))
                messages.success(request, "Факт подано дієтсестрі на перевірку.")
            else:
                messages.success(request, "Факт збережено.")
        except ValidationError as exc:
            for msg in exc.messages:
                messages.error(request, msg)
        except (PermissionDenied, wf.InvalidTransition):
            messages.error(request, "Факт зараз не можна змінювати.")
        return redirect("consumption:kitchen_day", day.isoformat())

    def render(self, request, plan, task, editable):
        actuals = list(ActualConsumption.objects.filter(plan=plan).order_by("product_name"))
        for a in actuals:
            dev = a.deviation_pct
            a.needs_reason = dev is not None and abs(dev) > services.DEVIATION_REASON_PCT
        used = {a.product_id for a in actuals}
        return render(request, "consumption/kitchen.html", {
            "plan": plan,
            "task": task,
            "board": services.kitchen_board(plan),
            "portions_total": sum(e.portions for e in plan.entries.all()),
            "entries": plan.entries.select_related("diet", "menu").order_by("diet__sort_order"),
            "actuals": actuals,
            "editable": editable,
            "extra_products": Product.objects.filter(is_active=True).exclude(pk__in=used).order_by("name") if editable else [],
            "can_review": task is not None and wf.can(request.user, task, "approve"),
            "rejection": task.last_rejection if task and task.status == TaskStatus.REJECTED else None,
            "threshold": services.DEVIATION_REASON_PCT,
            **day_nav(plan.service_date),
        })


class DishCardView(RoleRequiredMixin, View):
    """Card as frozen for the day, recalculated for N portions."""

    allowed_roles = roles_for("kitchen")

    def get(self, request, day, slot, card_no):
        plan = frozen_plan(parse_date(day))
        if plan is None:
            raise Http404
        n = request.GET.get("n", "")
        card = services.scaled_card(plan, slot, card_no, int(n) if n.isdigit() and 0 < int(n) <= 5000 else None)
        if card is None:
            raise Http404
        tpl = "consumption/_dish_card.html" if request.headers.get("HX-Request") else "consumption/dish_card.html"
        return render(request, tpl, {"plan": plan, "card": card})


# ====================================================================== S4-02 consumption
def parse_period(get):
    today = timezone.localdate()
    d_from = parse_date(get.get("from"), default=today)
    d_to = parse_date(get.get("to"), default=d_from)
    if d_to < d_from:
        d_from, d_to = d_to, d_from
    if (d_to - d_from).days > 366:
        raise Http404
    return d_from, d_to


class ConsumptionView(RoleRequiredMixin, View):
    allowed_roles = roles_for("consumption")

    def get(self, request):
        d_from, d_to = parse_period(request.GET)
        group = request.GET.get("group") if request.GET.get("group") in services.GROUPS else "product"
        data = services.period_consumption(d_from, d_to, group)
        if request.GET.get("format") == "xlsx":
            from apps.reports.exports import consumption_xlsx

            return consumption_xlsx(data, d_from, d_to, group)
        today = timezone.localdate()
        return render(request, "consumption/report.html", {
            "data": data, "d_from": d_from, "d_to": d_to, "group": group, "groups": services.GROUPS,
            "presets": [
                ("Сьогодні", today, today),
                ("Завтра", today + timedelta(days=1), today + timedelta(days=1)),
                ("Цей місяць", today.replace(day=1), today),
            ],
        })


# ====================================================================== S4-03 write-off
def default_write_off_date():
    """Oldest write-off still waiting for the accountant, else yesterday."""
    from apps.workflow.definitions import WRITE_OFF
    from apps.workflow.models import OPEN_FOR_MAKER, Task

    task = Task.objects.filter(type=WRITE_OFF, status__in=OPEN_FOR_MAKER).order_by("service_date").first()
    return task.service_date if task else timezone.localdate() - timedelta(days=1)


class WriteOffView(RoleRequiredMixin, View):
    allowed_roles = roles_for("write-off")

    def get(self, request, day=None):
        day = parse_date(day, default=default_write_off_date())
        plan = frozen_plan(day)
        task = services.write_off_task(day)
        if plan is None or task is None:
            cooking = services.cooking_task(plan) if plan else None
            return render(request, "consumption/write_off_empty.html", {
                "day": day, "plan": plan, "cooking": cooking, **day_nav(day)})
        wo = services.open_write_off(plan)
        rows = services.write_off_rows(wo)
        editable = services.write_off_editable(wo, request.user)
        return render(request, "consumption/write_off.html", {
            "plan": plan, "wo": wo, "task": wo.task, "rows": rows,
            "total": sum((r["cost"] or 0 for r in rows), Decimal(0)),
            "missing": [r for r in rows if r["price_per_kg"] is None],
            "editable": editable,
            "can_edit_prices": editable and request.user.has_role(*PRICE_EDITORS),
            "can_review": wf.can(request.user, wo.task, "approve"),
            "rejection": wo.task.last_rejection if wo.task.status == TaskStatus.REJECTED else None,
            **day_nav(day),
        })

    def post(self, request, day=None):
        day = parse_date(day, default=default_write_off_date())
        plan = frozen_plan(day)
        if plan is None or services.write_off_task(day) is None:
            raise Http404
        wo = services.open_write_off(plan)
        prices = {}
        for key, raw in request.POST.items():
            if key.startswith("price_") and key[6:].isdigit() and raw.strip():
                try:
                    value = Decimal(raw.strip().replace(",", "."))
                except InvalidOperation:
                    messages.error(request, "Ціна має бути числом.")
                    return redirect("consumption:write_off_day", day.isoformat())
                if value < 0:
                    messages.error(request, "Ціна не може бути від'ємною.")
                    return redirect("consumption:write_off_day", day.isoformat())
                prices[int(key[6:])] = value.quantize(Decimal("0.01"))
        try:
            if prices:
                changed = services.set_prices(wo, request.user, prices)
                if changed:
                    messages.success(request, f"Ціни оновлено: {changed}.")
            if request.POST.get("action") == "submit":
                wf.submit(wo.task, request.user, request.POST.get("comment", ""))
                messages.success(request, "Списання подано дієтсестрі на звірку.")
        except ValidationError as exc:
            for msg in exc.messages:
                messages.error(request, msg)
        except (PermissionDenied, wf.InvalidTransition):
            messages.error(request, "Списання зараз не можна змінювати.")
        return redirect("consumption:write_off_day", day.isoformat())
