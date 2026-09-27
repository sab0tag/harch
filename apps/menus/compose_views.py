"""AI-08 screens: «Підігнати під норму», «Нове меню», optional AI review of a combination."""
import json

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.accounts.access import RoleRequiredMixin
from apps.catalog.models import Diet
from apps.core.navigation import CATALOG_EDITORS

from .composer import Composer, replace_items, save_new_menu
from .models import Menu


def read_items(post):
    try:
        items = json.loads(post.get("items", ""))
    except ValueError:
        return None
    return items if isinstance(items, list) else None


class MenuFitView(RoleRequiredMixin, View):
    allowed_roles = CATALOG_EDITORS

    def get(self, request, number):
        menu = get_object_or_404(Menu.objects.select_related("diet"), number=number)
        composer = Composer(menu.diet)
        base, options = composer.fit_options(menu)
        return render(request, "menus/fit.html", {
            "menu": menu,
            "current": composer.describe(base),
            "options": [composer.describe(o) for o in options],
            "short": None if any(composer.in_norm(o) for o in options) else composer.reachable_hint(),
            "fit_buttons": [("apply", f"Застосувати до №{menu.number}", "btn-success"),
                            ("new", "Зберегти як нове меню", "btn-outline-primary")],
        })

    def post(self, request, number):
        menu = get_object_or_404(Menu.objects.select_related("diet"), number=number)
        items = read_items(request.POST)
        if items is None:
            raise Http404
        try:
            if request.POST.get("action") == "new":
                new = save_new_menu(menu.diet, items, title=f"На основі №{menu.number}, підігнано під норму")
                messages.success(request, f"Створено меню №{new.number} (не перевірено).")
                return redirect("menus:detail", new.number)
            replace_items(menu, items)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("menus:fit", menu.number)
        messages.success(request, f"Меню №{menu.number} змінено. Попередній склад збережено в історії; меню позначено «Не перевірено».")
        return redirect("menus:detail", menu.number)


class MenuGenerateView(RoleRequiredMixin, View):
    allowed_roles = CATALOG_EDITORS

    def get(self, request):
        diets = Diet.objects.filter(has_own_menus=True, is_active=True).order_by("sort_order")
        diet = diets.filter(code=request.GET.get("diet", "")).first()
        ctx = {"diets": diets, "diet": diet}
        if diet:
            seed = request.GET.get("seed", "")
            seed = int(seed) if seed.isdigit() else 1
            composer = Composer(diet)
            options = composer.generate(count=3, seed=seed)
            ctx.update({
                "options": [composer.describe(o) for o in options],
                "seed": seed,
                "short": None if any(composer.in_norm(o) for o in options) else composer.reachable_hint(),
                "generate_buttons": [("create", "Створити меню", "btn-success")],
            })
        return render(request, "menus/generate.html", ctx)

    def post(self, request):
        diet = get_object_or_404(Diet, code=request.POST.get("diet", ""), has_own_menus=True)
        items = read_items(request.POST)
        if items is None:
            raise Http404
        try:
            menu = save_new_menu(diet, items, title="Згенеровано під норму дієти")
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(f"/menus/new/?diet={diet.code}")
        messages.success(request, f"Створено меню №{menu.number} (не перевірено).")
        return redirect("menus:detail", menu.number)


class MenuReviewView(RoleRequiredMixin, View):
    """HTMX: AI comments on the combination (taste, variety, suitability). Numbers are not its job."""

    allowed_roles = CATALOG_EDITORS

    def post(self, request):
        from apps.assist import client
        from apps.assist.features import review_menu

        diet = get_object_or_404(Diet, code=request.POST.get("diet", ""))
        items = read_items(request.POST) or []
        try:
            review = review_menu(request.user, diet, items)
        except client.AIUnavailable:
            return render(request, "menus/_review.html", {"error": "ШІ вимкнено."})
        except (client.AIError, client.AILimitExceeded, ValueError) as exc:
            return render(request, "menus/_review.html", {"error": str(exc)})
        return render(request, "menus/_review.html", {"review": review})
