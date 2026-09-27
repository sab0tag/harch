from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, render
from django.views import View

from apps.accounts.access import RoleRequiredMixin
from apps.catalog.models import Diet, DishIngredient, MealSlot
from apps.catalog.nutrition import Nutrition, annotate_menu_nutrition, dish_nutrition, norm_status
from apps.core.navigation import roles_for

from .models import Menu, MenuItem

NORM_FIELDS = (("kcal", "Ккал"), ("protein", "Білки, г"), ("fat", "Жири, г"), ("carbs", "Вуглеводи, г"))


def norm_rows(nutrition, diet):
    """[{label, value, low, high, status}] for the four nutrition values vs the diet norm."""
    rows = []
    for field, label in NORM_FIELDS:
        low, high = diet.norm(field)
        value = getattr(nutrition, field)
        rows.append({"field": field, "label": label, "value": value, "low": low, "high": high,
                     "status": norm_status(value, low, high)})
    return rows


class MenuListView(RoleRequiredMixin, View):
    allowed_roles = roles_for("menus")

    def get(self, request):
        diet_code = request.GET.get("diet", "")
        status = request.GET.get("status", "")
        q = request.GET.get("q", "").strip()
        qs = Menu.objects.select_related("diet").filter(is_active=True)
        if diet_code:
            qs = qs.filter(diet__code=diet_code)
        if q.isdigit():
            qs = qs.filter(number=int(q))
        if request.GET.get("state") == "unverified":
            qs = qs.filter(verified=False)
        menus = list(annotate_menu_nutrition(qs).order_by("number"))
        for m in menus:
            n = Nutrition(m.n_protein, m.n_fat, m.n_carbs, m.n_kcal)
            m.norms = {r["field"]: r for r in norm_rows(n, m.diet)}
            m.kcal_status = m.norms["kcal"]["status"]
        summary = {s: sum(1 for m in menus if m.kcal_status == s) for s in ("ok", "warn", "bad")}
        if status in ("ok", "warn", "bad"):
            menus = [m for m in menus if m.kcal_status == status]
        page = Paginator(menus, 50).get_page(request.GET.get("page"))
        return render(request, "menus/menu_list.html", {
            "page": page,
            "diets": Diet.objects.filter(has_own_menus=True),
            "filters": {"diet": diet_code, "status": status, "q": q, "state": request.GET.get("state", "")},
            "summary": summary,
        })


class MenuDetailView(RoleRequiredMixin, View):
    allowed_roles = roles_for("menus")

    def get(self, request, number):
        ingredients = DishIngredient.objects.select_related("product")
        items = MenuItem.objects.select_related("dish").prefetch_related(
            Prefetch("dish__ingredients", queryset=ingredients)
        ).order_by("sort_order", "id")
        menu = get_object_or_404(
            Menu.objects.select_related("diet").prefetch_related(Prefetch("items", queryset=items)), number=number
        )
        slots, total = [], Nutrition()
        by_slot = {}
        for item in menu.items.all():
            n = dish_nutrition(item.dish)
            total += n
            by_slot.setdefault(item.meal_slot, []).append({"item": item, "n": n.rounded()})
        for value, label in MealSlot.choices:
            if value in by_slot:
                slot_total = Nutrition()
                for row in by_slot[value]:
                    slot_total += row["n"]
                slots.append({"label": label, "rows": by_slot[value], "total": slot_total})
        neighbours = Menu.objects.filter(diet=menu.diet, is_active=True).order_by("number")
        prev_menu = neighbours.filter(number__lt=menu.number).last()
        next_menu = neighbours.filter(number__gt=menu.number).first()
        return render(request, "menus/menu_detail.html", {
            "menu": menu, "slots": slots, "norms": norm_rows(total.rounded(), menu.diet),
            "prev_menu": prev_menu, "next_menu": next_menu,
        })
