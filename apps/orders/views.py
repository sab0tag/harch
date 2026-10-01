from datetime import date, timedelta

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from apps.accounts.access import RoleRequiredMixin
from apps.accounts.models import Role
from apps.core.navigation import roles_for
from apps.orgstructure.models import Department
from apps.workflow import services as wf
from apps.workflow.definitions import MENU_PLANNING, ORDER_SUBMISSION, order_deadline
from apps.workflow.models import CLOSED, Task, TaskStatus

from . import corrections, services
from .models import DailyOrder, OrderCorrection


def parse_date(value, default=None):
    if not value:
        return default or services.default_service_date()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise Http404 from exc


def read_portions(post, diets):
    portions = {}
    for diet in diets:
        raw = (post.get(f"diet_{diet.pk}") or "").strip()
        if raw == "":
            portions[diet.pk] = 0
            continue
        if not raw.isdigit():
            raise ValidationError(f"{diet.code}: вкажіть ціле число порцій.")
        portions[diet.pk] = int(raw)
    return portions


class OrdersEntryView(RoleRequiredMixin, View):
    """Nurse → own department's order; dietician/admin → summary."""

    allowed_roles = roles_for("orders")

    def get(self, request):
        day = parse_date(request.GET.get("date"))
        if request.user.role == Role.DEPT_NURSE:
            return redirect("orders:edit", request.user.department_id, day.isoformat())
        return redirect(reverse("orders:summary") + f"?date={day.isoformat()}")


class OrderNotOpened(Exception):
    def __init__(self, department):
        super().__init__(department)
        self.department = department


class OrderEditView(RoleRequiredMixin, View):
    allowed_roles = roles_for("orders")

    def get_order(self, request, dept_id, day):
        user = request.user
        if user.role == Role.DEPT_NURSE and user.department_id != dept_id:
            raise Http404  # other departments' orders are not revealed
        # A switched-off department's orders stay readable (notifications and history link to them),
        # but nothing new is opened for it.
        department = get_object_or_404(Department, pk=dept_id)
        # A nurse opens (creates) orders only for today onwards; a past day is view-only for her.
        # An admin may still open a past day to catch it up (docs/08_ops.md).
        if department.is_active and services.opens_on(day) <= timezone.localdate() and (
                user.role == Role.ADMIN or (user.role == Role.DEPT_NURSE and day >= timezone.localdate())):
            return services.open_order(department, day)
        order = DailyOrder.objects.filter(department=department, service_date=day).select_related("task").first()
        if order is None:
            raise OrderNotOpened(department)
        return order

    def get(self, request, dept_id, day):
        day = parse_date(day)
        try:
            order = self.get_order(request, dept_id, day)
        except OrderNotOpened as missing:
            # Day arrows lead here too: an order the nurse has not opened yet is a normal state, not a 404.
            task = Task.objects.filter(type=ORDER_SUBMISSION, service_date=day, department=missing.department).first()
            return render(request, "orders/order_missing.html", {
                "department": missing.department, "day": day, "task": task,
                "task_closed": task is not None and task.status in CLOSED,
                "day_over": day < timezone.localdate(),
                "opens_on": services.opens_on(day) if services.opens_on(day) > timezone.localdate() else None,
                "prev_day": day - timedelta(days=1), "next_day": day + timedelta(days=1),
            })
        return self.render(request, order)

    def post(self, request, dept_id, day):
        day = parse_date(day)
        try:
            order = self.get_order(request, dept_id, day)
        except OrderNotOpened as exc:
            raise Http404 from exc     # only the nurse (or admin) opens an order
        diets = list(services.orderable_diets())
        action = request.POST.get("action", "save")
        if action == "reopen":
            try:
                wf.reopen(order.task, request.user)
                messages.success(request, "Замовлення відкрито заново — заповніть і подайте його.")
            except (PermissionDenied, wf.InvalidTransition):
                messages.error(request, "Це замовлення не можна відкрити заново.")
            return redirect("orders:edit", dept_id, day.isoformat())
        no_patients = request.POST.get("no_patients") == "on"
        note = request.POST.get("note", "").strip()
        try:
            portions = read_portions(request.POST, diets)
            if action == "submit":
                services.submit_order(order, request.user, portions, no_patients=no_patients, note=note)
                messages.success(request, "Замовлення подано на перевірку дієтсестрі.")
            else:
                services.save_order(order, request.user, portions, no_patients=no_patients, note=note)
                messages.success(request, "Чернетку збережено.")
        except ValidationError as exc:
            for msg in exc.messages:
                messages.error(request, msg)
            return self.render(request, order, posted=request.POST)
        except (PermissionDenied, wf.InvalidTransition):
            if order.task and wf.day_is_over(order.task) and request.user.role != Role.ADMIN:
                messages.error(request, "Дата минула — замовлення можна лише переглянути.")
            else:
                messages.error(request, "Замовлення вже подано — редагування недоступне.")
        return redirect("orders:edit", dept_id, day.isoformat())

    def render(self, request, order, posted=None):
        order.refresh_from_db()
        diets = list(services.orderable_diets())
        default = order.department.default_diet_id
        diets.sort(key=lambda d: (d.pk != default,))
        values = services.portions_by_diet(order)
        rows = []
        for d in diets:
            value = posted.get(f"diet_{d.pk}", "") if posted else values.get(d.pk, "")
            rows.append({"diet": d, "value": value, "is_default": d.pk == default})
        task = order.task
        return render(request, "orders/order_form.html", {
            "order": order,
            "task": task,
            "rows": rows,
            "editable": services.is_editable_by(order, request.user),
            "total": sum(values.values()),
            "rejection": task.last_rejection if task and task.status == TaskStatus.REJECTED else None,
            "overdue": task.is_overdue() if task else False,
            "day_over": order.service_date < timezone.localdate(),
            "prev_day": order.service_date - timedelta(days=1),
            "next_day": order.service_date + timedelta(days=1),
            "can_review": task is not None and wf.can(request.user, task, "approve"),
            "can_reopen": task is not None and wf.can(request.user, task, "reopen"),
            "corrections": corrections.corrections_for(order.service_date, order.department),
            "can_correct": (task is not None and task.status == TaskStatus.APPROVED
                            and corrections.can_create(request.user, order.department)
                            and corrections.allowed_meals(order.department, order.service_date)[1] is None),
        })


class OrderSummaryView(RoleRequiredMixin, View):
    allowed_roles = (Role.DIETICIAN,)

    def get(self, request):
        day = parse_date(request.GET.get("date"))
        departments = list(Department.objects.filter(is_active=True).select_related("default_diet"))
        rows = services.summary(day, departments)
        diets = list(services.orderable_diets())
        totals = {d.pk: sum(r["portions"].get(d.pk, 0) for r in rows) for d in diets}
        approved = services.approved_portions(day, include_late=True)
        counts = {s: 0 for s in ("missing", "open", "submitted", "approved", "cancelled")}
        for r in rows:
            t = r["task"]
            key = "missing" if t is None else {
                TaskStatus.SUBMITTED: "submitted", TaskStatus.APPROVED: "approved", TaskStatus.CANCELLED: "cancelled",
            }.get(t.status, "open")
            r["state"] = key
            r["can_review"] = key == "submitted" and wf.can(request.user, t, "approve")  # checker, never own
            counts[key] += 1
        counts["approvable"] = sum(r["can_review"] for r in rows)
        plan_task = Task.objects.filter(type=MENU_PLANNING, service_date=day).first()
        day_corrections = list(corrections.corrections_for(day))
        for c in day_corrections:
            c.can_review = c.task is not None and wf.can(request.user, c.task, "approve")
        return render(request, "orders/summary.html", {
            "corrections": day_corrections,
            "day_over": day < timezone.localdate() and request.user.role != Role.ADMIN,
            # a plan from only the approved orders makes sense once the others are late, not before
            "can_force_plan": timezone.now() >= order_deadline(day),
            "order_deadline": order_deadline(day),
            "day": day,
            "rows": rows,
            "diets": diets,
            "totals": totals,
            "approved": approved,
            "approved_total": sum(approved.values()),
            "counts": counts,
            "plan_task": plan_task,
            "prev_day": day - timedelta(days=1),
            "next_day": day + timedelta(days=1),
        })

    def post(self, request):
        day = parse_date(request.POST.get("date"))
        action = request.POST.get("action")
        back = redirect(reverse("orders:summary") + f"?date={day.isoformat()}")
        if action in ("create_tasks", "force_plan") and day < timezone.localdate() and request.user.role != Role.ADMIN:
            messages.error(request, "Дата минула: задачі й план на неї створює лише адміністратор.")
            return back
        if action in ("create_tasks", "force_plan") and services.opens_on(day) > timezone.localdate():
            messages.error(request, f"Замовлення на {day:%d.%m} відкриються {services.opens_on(day):%d.%m}.")
            return back
        if action == "create_tasks":
            created = sum(
                wf.ensure_task(ORDER_SUBMISSION, day, department=d)[1]
                for d in Department.objects.filter(is_active=True)
            )
            messages.success(request, f"Створено задач: {created}.")
        elif action == "approve_all":
            done, failed = 0, 0
            for task in Task.objects.filter(type=ORDER_SUBMISSION, service_date=day, status=TaskStatus.SUBMITTED):
                try:
                    wf.approve(task, request.user)
                    done += 1
                except (PermissionDenied, wf.InvalidTransition, ValidationError):
                    failed += 1
            messages.success(request, f"Затверджено замовлень: {done}.")
            if failed:
                messages.warning(request, f"Не вдалося затвердити: {failed} (наприклад, ви самі їх подали).")
        elif action == "force_plan":
            from apps.planning.services import force_planning

            if timezone.now() < order_deadline(day):
                messages.error(request, f"Сформувати план без усіх замовлень можна після терміну подачі "
                                        f"({timezone.localtime(order_deadline(day)):%d.%m %H:%M}).")
                return back
            try:
                force_planning(day, request.user)
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
                return back
            messages.success(request, "План меню сформовано з уже затверджених замовлень.")
            return redirect("planning:plan", day.isoformat())
        return back


# ------------------------------------------------------------------ S7-01 corrections
def read_deltas(post, diets):
    deltas = {}
    for diet in diets:
        raw = (post.get(f"diet_{diet.pk}") or "").strip().replace("−", "-").replace(" ", "")
        if raw in ("", "0", "+0", "-0"):
            continue
        try:
            deltas[diet.pk] = int(raw)
        except ValueError as exc:
            raise ValidationError(f"{diet.code}: вкажіть ціле число, наприклад +2 або -1.") from exc
    return deltas


def read_transfer_out(post, diets):
    """«Знято там» column of a transfer: portions taken off the sending department, per diet (sign ignored)."""
    out = {}
    for diet in diets:
        raw = (post.get(f"out_{diet.pk}") or "").strip().replace("−", "-").replace(" ", "").lstrip("+-")
        if raw in ("", "0"):
            continue
        if not raw.isdigit():
            raise ValidationError(f"{diet.code}: у колонці «знято там» вкажіть ціле число.")
        out[diet.pk] = int(raw)
    return out


class CorrectionNewView(RoleRequiredMixin, View):
    allowed_roles = roles_for("orders")

    def post(self, request, dept_id, day):
        day = parse_date(day)
        department = get_object_or_404(Department, pk=dept_id, is_active=True)
        try:
            correction = corrections.open_correction(department, day, request.user)
        except (ValidationError, PermissionDenied) as exc:
            for msg in getattr(exc, "messages", [str(exc)]):
                messages.error(request, msg)
            return redirect("orders:edit", dept_id, day.isoformat())
        return redirect("orders:correction", correction.pk)


class CorrectionView(RoleRequiredMixin, View):
    allowed_roles = roles_for("orders")

    def get_correction(self, request, pk):
        correction = get_object_or_404(OrderCorrection.objects.select_related("department", "task", "transfer_from"), pk=pk)
        user = request.user
        if user.role == Role.DEPT_NURSE and user.department_id not in (correction.department_id, correction.transfer_from_id):
            raise Http404
        return correction

    def get(self, request, pk):
        return self.render(request, self.get_correction(request, pk))

    def post(self, request, pk):
        correction = self.get_correction(request, pk)
        diets = list(corrections.available_diets(correction.service_date))
        action = request.POST.get("action", "save")
        transfer_id = request.POST.get("transfer_from") or None
        fields = {
            "from_meal": request.POST.get("from_meal", ""),
            "reason": request.POST.get("reason", ""),
            "transfer_from": Department.objects.filter(pk=transfer_id, is_active=True).first() if transfer_id else None,
            "note": request.POST.get("note", "").strip(),
        }
        try:
            deltas = read_deltas(request.POST, diets)
            fields["transfer_out"] = read_transfer_out(request.POST, diets)
            if action == "submit":
                corrections.submit_correction(correction, request.user, deltas, **fields)
                messages.success(request, "Коригування подано дієтсестрі.")
            else:
                corrections.save_correction(correction, request.user, deltas, **fields)
                messages.success(request, "Чернетку збережено.")
        except ValidationError as exc:
            for msg in exc.messages:
                messages.error(request, msg)
            return self.render(request, correction, posted=request.POST)
        except (PermissionDenied, wf.InvalidTransition) as exc:
            messages.error(request, str(exc) or "Коригування вже подано — редагування недоступне.")
        return redirect("orders:correction", correction.pk)

    def render(self, request, correction, posted=None):
        correction.refresh_from_db()
        day, dept = correction.service_date, correction.department
        meals, why = corrections.allowed_meals(dept, day)
        own = {l.diet_id: l.delta for l in correction.lines.all() if l.department_id == dept.pk}
        out = {l.diet_id: -l.delta for l in correction.lines.all()
               if correction.transfer_from_id and l.department_id == correction.transfer_from_id}
        have = corrections.department_portions(dept, day, exclude=correction)
        rows = []
        planned = corrections.planned_diet_ids(day)
        for d in corrections.available_diets(day):
            value = posted.get(f"diet_{d.pk}", "") if posted else (f"{own[d.pk]:+d}" if d.pk in own else "")
            menu = None if d.pk in planned else corrections.menu_for(d, day)
            out_value = posted.get(f"out_{d.pk}", "") if posted else (f"−{out[d.pk]}" if d.pk in out else "")
            rows.append({"diet": d, "value": value, "out_value": out_value, "have": have.get(d.pk, 0),
                         "outside_plan": d.pk not in planned, "cycle_menu": menu})
        task = correction.task
        return render(request, "orders/correction.html", {
            "correction": correction,
            "task": task,
            "rows": rows,
            "lines": correction.lines.select_related("department", "diet"),
            "editable": corrections.is_editable_by(correction, request.user),
            "meals": [(m, corrections.SLOT_LABELS[m]) for m in meals],
            "closed_reason": why,
            "reasons": corrections.REASONS_FOR_NURSE,
            "departments": Department.objects.filter(is_active=True).exclude(pk=dept.pk),
            "posted": posted,
            "rejection": task.last_rejection if task and task.status == TaskStatus.REJECTED else None,
            "can_review": task is not None and wf.can(request.user, task, "approve"),
            "cutoff": settings.CORRECTION_LUNCH_CUTOFF,
        })
