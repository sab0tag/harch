from itertools import chain

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Max, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator

from apps.accounts.access import RoleRequiredMixin
from apps.core.navigation import CATALOG_EDITORS, PRICE_EDITORS, roles_for

from .forms import DishForm, IngredientForm, ProductForm, ProductPriceForm
from .models import Diet, Dish, DishIngredient, Product
from .nutrition import annotate_dish_nutrition, dish_nutrition, ingredient_nutrition


def can_edit_catalog(user):
    return user.has_role(*CATALOG_EDITORS)


# ---------------------------------------------------------------- dishes
class DishListView(RoleRequiredMixin, View):
    allowed_roles = roles_for("dishes")

    def get(self, request):
        qs = Dish.objects.prefetch_related("diets")
        q = request.GET.get("q", "").strip()
        category = request.GET.get("category", "")
        diet = request.GET.get("diet", "")
        state = request.GET.get("state", "")
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(card_no=int(q))) if q.isdigit() else qs.filter(name__icontains=q)
        if category:
            qs = qs.filter(category=category)
        if diet:
            qs = qs.filter(diets__code=diet)
        if state == "unverified":
            qs = qs.filter(verified=False)
        elif state == "inactive":
            qs = qs.filter(is_active=False)
        else:
            qs = qs.filter(is_active=True)
        qs = annotate_dish_nutrition(qs.distinct()).order_by("card_no")
        page = Paginator(qs, 50).get_page(request.GET.get("page"))
        return render(request, "catalog/dish_list.html", {
            "page": page,
            "categories": Dish.objects.order_by("category").values_list("category", flat=True).distinct(),
            "diets": Diet.objects.all(),
            "filters": {"q": q, "category": category, "diet": diet, "state": state},
            "can_edit": can_edit_catalog(request.user),
            "unverified_total": Dish.objects.filter(verified=False, is_active=True).count(),
        })


def _row(ing, form=None, editable=True):
    if editable and form is None:
        form = IngredientForm(instance=ing, prefix=f"i{ing.pk}")
    return {"ing": ing, "n": ingredient_nutrition(ing).rounded(), "form": form}


def _dish_context(dish, user):
    editable = can_edit_catalog(user)
    ingredients = list(dish.ingredients.select_related("product"))
    rows = [_row(i, editable=editable) for i in ingredients]
    history = sorted(
        chain(
            dish.history.select_related("history_user")[:20],
            DishIngredient.history.filter(dish_id=dish.pk).select_related("history_user", "product")[:40],
        ),
        key=lambda h: h.history_date,
        reverse=True,
    )[:25]
    return {
        "dish": dish,
        "rows": rows,
        "totals": dish_nutrition(dish).rounded(),
        "can_edit": editable,
        "add_form": IngredientForm(dish=dish, prefix="new"),
        "history": history,
    }


class DishDetailView(RoleRequiredMixin, View):
    allowed_roles = roles_for("dishes")

    def get(self, request, pk):
        dish = get_object_or_404(Dish.objects.prefetch_related("diets"), pk=pk)
        return render(request, "catalog/dish_detail.html", _dish_context(dish, request.user))


class DishEditView(RoleRequiredMixin, View):
    allowed_roles = CATALOG_EDITORS

    def get_object(self, pk):
        return get_object_or_404(Dish, pk=pk) if pk else None

    def get(self, request, pk=None):
        dish = self.get_object(pk)
        initial = {} if dish else {"card_no": (Dish.objects.aggregate(m=Max("card_no"))["m"] or 0) + 1}
        return self.render(request, DishForm(instance=dish, initial=initial), dish)

    def post(self, request, pk=None):
        dish = self.get_object(pk)
        form = DishForm(request.POST, instance=dish)
        if not form.is_valid():
            return self.render(request, form, dish)
        dish = form.save()
        messages.success(request, "Картку збережено.")
        return redirect("catalog:dish_detail", pk=dish.pk)

    def render(self, request, form, dish):
        categories = Dish.objects.order_by("category").values_list("category", flat=True).distinct()
        return render(request, "catalog/dish_form.html", {"form": form, "dish": dish, "categories": categories})


@method_decorator(require_POST, name="dispatch")
class DishVerifyView(RoleRequiredMixin, View):
    allowed_roles = CATALOG_EDITORS

    def post(self, request, pk):
        dish = get_object_or_404(Dish, pk=pk)
        dish.verified = request.POST.get("verified", "1") == "1"
        dish.save(update_fields=["verified", "updated_at"])
        messages.success(request, "Позначено як перевірено." if dish.verified else "Позначку «перевірено» знято.")
        return redirect("catalog:dish_detail", pk=pk)


# ------------------------------------------------ ingredient rows (HTMX)
def _card_response(request, dish, *, row_forms=None, add_form=None):
    """Re-render the whole ingredients card: rows, totals and add form stay consistent."""
    dish = Dish.objects.get(pk=dish.pk)
    row_forms = row_forms or {}
    rows = [_row(i, row_forms.get(i.pk)) for i in dish.ingredients.select_related("product")]
    return render(request, "catalog/_ingredients_card.html", {
        "dish": dish,
        "rows": rows,
        "totals": dish_nutrition(dish).rounded(),
        "can_edit": True,
        "add_form": add_form or IngredientForm(dish=dish, prefix="new"),
    })


class IngredientAddView(RoleRequiredMixin, View):
    allowed_roles = CATALOG_EDITORS

    def post(self, request, pk):
        dish = get_object_or_404(Dish, pk=pk)
        form = IngredientForm(request.POST, dish=dish, prefix="new")
        if not form.is_valid():
            return _card_response(request, dish, add_form=form)
        ing = form.save(commit=False)
        ing.dish = dish
        ing.sort_order = (dish.ingredients.aggregate(m=Max("sort_order"))["m"] or 0) + 1
        ing.save()
        return _card_response(request, dish)


class IngredientUpdateView(RoleRequiredMixin, View):
    allowed_roles = CATALOG_EDITORS

    def post(self, request, pk, ing_pk):
        ing = get_object_or_404(DishIngredient.objects.select_related("dish"), pk=ing_pk, dish_id=pk)
        form = IngredientForm(request.POST, instance=ing, prefix=f"i{ing.pk}")
        if form.is_valid():
            form.save()
            return _card_response(request, ing.dish)
        return _card_response(request, ing.dish, row_forms={ing.pk: form})


class IngredientDeleteView(RoleRequiredMixin, View):
    allowed_roles = CATALOG_EDITORS

    def post(self, request, pk, ing_pk):
        ing = get_object_or_404(DishIngredient.objects.select_related("dish"), pk=ing_pk, dish_id=pk)
        dish = ing.dish
        ing.delete()
        return _card_response(request, dish)


# ---------------------------------------------------------------- products
class ProductListView(RoleRequiredMixin, View):
    allowed_roles = roles_for("products")

    def get(self, request):
        q = request.GET.get("q", "").strip()
        qs = Product.objects.select_related("category").annotate(dish_count=Count("dishingredient__dish", distinct=True))
        if q:
            qs = qs.filter(name__icontains=q)
        return render(request, "catalog/product_list.html", {
            "products": qs.order_by("name"),
            "q": q,
            "can_edit": request.user.has_role(*PRICE_EDITORS),
            "full_edit": can_edit_catalog(request.user),
        })


class ProductEditView(RoleRequiredMixin, View):
    """Dietician/admin: full edit and create. Accountant: price only, no create."""

    allowed_roles = PRICE_EDITORS

    def dispatch(self, request, *args, **kwargs):
        if kwargs.get("pk") is None and request.user.is_authenticated and not can_edit_catalog(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def form_class(self, user):
        return ProductForm if can_edit_catalog(user) else ProductPriceForm

    def get_object(self, pk):
        return get_object_or_404(Product, pk=pk) if pk else None

    def get(self, request, pk=None):
        product = self.get_object(pk)
        return self.render(request, self.form_class(request.user)(instance=product), product)

    def post(self, request, pk=None):
        product = self.get_object(pk)
        form = self.form_class(request.user)(request.POST, instance=product)
        if not form.is_valid():
            return self.render(request, form, product)
        product = form.save()
        messages.success(request, f"Продукт «{product.name}» збережено.")
        return redirect(reverse("catalog:product_list") + f"?q={product.name}")

    def render(self, request, form, product):
        return render(request, "catalog/product_form.html", {
            "form": form, "product": product, "price_only": isinstance(form, ProductPriceForm),
        })


# ---------------------------------------------------------------- diets
class DietListView(RoleRequiredMixin, View):
    allowed_roles = roles_for("diets")

    def get(self, request):
        diets = Diet.objects.annotate(
            dish_count=Count("dishes", filter=Q(dishes__is_active=True), distinct=True),
            menu_count=Count("menus", filter=Q(menus__is_active=True), distinct=True),
        )
        return render(request, "catalog/diet_list.html", {"diets": diets})
