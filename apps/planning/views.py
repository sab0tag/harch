from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views import View

from apps.accounts.access import RoleRequiredMixin
from apps.catalog.nutrition import Nutrition, with_menu_nutrition
from apps.consumption.calc import CalcError, PlanInput, compute_lines, totals_by_product
from apps.consumption.services import snapshot_totals
from apps.core.navigation import roles_for
from apps.menus.models import Menu
from apps.menus.views import norm_rows
from apps.orders.views import parse_date
from apps.workflow import services as wf
from apps.workflow.definitions import ORDER_SUBMISSION
from apps.workflow.models import Task, TaskStatus

from . import services
from .models import DailyPlan


def read_menus(post, entries):
    """{diet_id: menu_id|None} from menu_<diet_id> fields."""
    result = {}
    for e in entries:
        raw = post.get(f"menu_{e.diet_id}")
        if raw is None:
            continue
        result[e.diet_id] = int(raw) if raw.isdigit() else None
    return result


def consumption_rows(entries_with_menus):
    """[(name, brutto_g, netto_g)] computed live (plan not approved yet)."""
    inputs = [PlanInput(menu.number, portions) for menu, portions in entries_with_menus if menu]
    try:
        totals = totals_by_product(compute_lines(inputs))
    except CalcError:
        return []
    return [(t.product_name, t.brutto_g, t.netto_g) for t in totals]


class PlanView(RoleRequiredMixin, View):
    allowed_roles = roles_for("plan")

    def get(self, request, day=None):
        day = parse_date(day, default=services.default_plan_date())
        plan_task = services.plan_task(day)
        plan = DailyPlan.objects.filter(service_date=day).first()
        if plan_task is None and plan is None:
            orders = Task.objects.filter(type=ORDER_SUBMISSION, service_date=day)
            return render(request, "planning/no_plan.html", {
                "day": day,
                "orders_total": orders.count(),
                "orders_done": orders.filter(status__in=[TaskStatus.APPROVED, TaskStatus.CANCELLED]).count(),
                "prev_day": day - timedelta(days=1), "next_day": day + timedelta(days=1),
            })
        plan = services.open_plan(day)
        editable = services.is_editable_by(plan, request.user)
        if editable:
            services.sync_portions(plan)
        return self.render(request, plan, editable)

    def post(self, request, day):
        day = parse_date(day)
        plan = services.open_plan(day)
        entries = list(plan.entries.all())
        menus = read_menus(request.POST, entries)
        try:
            if request.POST.get("action") == "submit":
                services.submit_plan(plan, request.user, menus, request.POST.get("comment", ""))
                messages.success(request, "План подано шеф-кухарю на перевірку.")
            else:
                services.set_menus(plan, request.user, menus)
                messages.success(request, "План збережено.")
        except ValidationError as exc:
            for msg in exc.messages:
                messages.error(request, msg)
        except (PermissionDenied, wf.InvalidTransition):
            messages.error(request, "План зараз не можна змінювати.")
        return redirect("planning:plan", day.isoformat())

    def render(self, request, plan, editable):
        task = plan.task
        entries = list(plan.entries.select_related("diet", "menu"))
        diet_ids = [e.diet_id for e in entries]
        options = {}
        for m in with_menu_nutrition(Menu.objects.filter(diet_id__in=diet_ids, is_active=True).order_by("number")):
            options.setdefault(m.diet_id, []).append(m)
        blocks = []
        for e in entries:
            chosen = next((m for m in options.get(e.diet_id, []) if e.menu_id == m.pk), None)
            norms = None
            if chosen:
                norms = norm_rows(Nutrition(chosen.n_protein, chosen.n_fat, chosen.n_carbs, chosen.n_kcal).rounded(), e.diet)
            blocks.append({"entry": e, "options": options.get(e.diet_id, []), "chosen": chosen, "norms": norms})
        if plan.is_frozen:
            consumption = snapshot_totals(plan)
        else:
            consumption = consumption_rows([(e.menu, e.portions) for e in entries])
        return render(request, "planning/plan.html", {
            "plan": plan,
            "task": task,
            "blocks": blocks,
            "editable": editable,
            "consumption": consumption,
            "consumption_total": sum((b for _, b, _ in consumption), Decimal(0)),
            "portions_total": sum(e.portions for e in entries),
            "rejection": task.last_rejection if task and task.status == TaskStatus.REJECTED else None,
            "can_review": task is not None and wf.can(request.user, task, "approve"),
            "drift": services.portions_drift(plan),
            "can_sign": services.can_sign(request.user, plan),
            "prev_day": plan.service_date - timedelta(days=1),
            "next_day": plan.service_date + timedelta(days=1),
        })


class PlanPreviewView(RoleRequiredMixin, View):
    """HTMX: consumption for the menus currently selected in the form, without saving."""

    allowed_roles = roles_for("plan")

    def get(self, request, day):
        plan = DailyPlan.objects.filter(service_date=parse_date(day)).first()
        rows = []
        if plan is not None:
            entries = list(plan.entries.select_related("menu"))
            chosen = read_menus(request.GET, entries)
            menus = {m.pk: m for m in Menu.objects.filter(pk__in=[v for v in chosen.values() if v])}
            pairs = []
            for e in entries:
                menu = menus.get(chosen.get(e.diet_id)) if e.diet_id in chosen else e.menu
                if menu and menu.diet_id == e.diet_id:
                    pairs.append((menu, e.portions))
            rows = consumption_rows(pairs)
        return render(request, "planning/_consumption.html", {
            "consumption": rows, "consumption_total": sum((b for _, b, _ in rows), Decimal(0)), "frozen": False,
        })


class SignView(RoleRequiredMixin, View):
    """«На підпис»: the director signs approved menu-rozkladkas in the system instead of on paper."""

    allowed_roles = roles_for("sign")

    def get(self, request):
        plans = services.to_sign()
        for p in plans:
            p.can_sign = services.can_sign(request.user, p)
        return render(request, "planning/sign.html", {"plans": plans, "waiting": sum(p.signed_at is None for p in plans)})

    def post(self, request):
        from django.contrib import messages
        from django.core.exceptions import PermissionDenied
        from django.shortcuts import get_object_or_404, redirect

        plan = get_object_or_404(DailyPlan, pk=request.POST.get("plan"))
        try:
            services.sign_plan(plan, request.user)
            messages.success(request, f"Меню-розкладку на {plan.service_date:%d.%m} підписано.")
        except PermissionDenied as exc:
            messages.error(request, str(exc))
        return redirect(request.POST.get("next") or "planning:sign")
