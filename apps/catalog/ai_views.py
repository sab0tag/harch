"""AI-07 «Чернетка картки» and AI-06 «Нові продукти (ШІ)». The model drafts; people accept."""
from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.accounts.access import RoleRequiredMixin
from apps.assist import client
from apps.assist.models import AIDraft
from apps.core.navigation import CATALOG_EDITORS

from . import drafts
from .audit import normalize
from .models import Diet, Dish, Product, ProductCategory

EXTRA_ROWS = 3


def ai_error(request, exc):
    if isinstance(exc, client.AIUnavailable):
        messages.error(request, "ШІ вимкнено або не налаштовано (сторінка «ШІ-помічник»).")
    else:
        messages.error(request, str(exc))


def own_draft(request, pk, kind):
    draft = get_object_or_404(AIDraft, pk=pk, kind=kind)
    if draft.user_id != request.user.pk and request.user.role != "admin":
        raise Http404
    return draft


# ====================================================================== AI-07
class DishDraftStartView(RoleRequiredMixin, View):
    allowed_roles = CATALOG_EDITORS

    def get(self, request):
        return render(request, "catalog/dish_draft_start.html", {
            "diets": Diet.objects.filter(is_active=True), "categories": categories(),
            "form": {"name": "", "category": "", "output_g": "", "diets": [], "wishes": ""},
        })

    def post(self, request):
        form = {
            "name": request.POST.get("name", "").strip()[:200],
            "category": request.POST.get("category", "").strip()[:100],
            "output_g": request.POST.get("output_g", "").strip(),
            "diets": [int(d) for d in request.POST.getlist("diets") if d.isdigit()],
            "wishes": request.POST.get("wishes", "").strip()[:500],
        }
        if not form["name"]:
            messages.error(request, "Вкажіть назву страви.")
            return render(request, "catalog/dish_draft_start.html", {
                "diets": Diet.objects.filter(is_active=True), "categories": categories(), "form": form})
        try:
            result = client_draft_dish(request.user, form)
        except (client.AIUnavailable, client.AIError, client.AILimitExceeded) as exc:
            ai_error(request, exc)
            return render(request, "catalog/dish_draft_start.html", {
                "diets": Diet.objects.filter(is_active=True), "categories": categories(), "form": form})
        draft = AIDraft.objects.create(kind=AIDraft.Kind.DISH, user=request.user, input=form, result=result)
        return redirect("catalog:dish_draft", draft.pk)


def client_draft_dish(user, form):
    from apps.assist.features import draft_dish

    return draft_dish(user, name=form["name"], category=form["category"], output_g=form["output_g"],
                      diets=form["diets"], wishes=form["wishes"])


def categories():
    return [c for c in Dish.objects.order_by("category").values_list("category", flat=True).distinct() if c]


class DishDraftView(RoleRequiredMixin, View):
    allowed_roles = CATALOG_EDITORS

    def get(self, request, pk):
        draft = own_draft(request, pk, AIDraft.Kind.DISH)
        products = {p.pk: p for p in Product.objects.filter(is_active=True)}
        rows = [{"product": products.get(r["product_id"]), "brutto_g": r["brutto_g"], "netto_g": r["netto_g"]}
                for r in draft.result.get("ingredients", []) if r["product_id"] in products]
        values = {
            "card_no": drafts.next_card_no(), "name": draft.input.get("name", ""),
            "category": draft.input.get("category", ""),
            "output_g": draft.input.get("output_g") or draft.result.get("output_g") or "",
            "diets": draft.input.get("diets", []), "technology": draft.result.get("technology", ""),
        }
        return self.render(request, draft, rows, values, [])

    def post(self, request, pk):
        draft = own_draft(request, pk, AIDraft.Kind.DISH)
        action = request.POST.get("action")
        if action == "discard":
            draft.status = AIDraft.Status.DISCARDED
            draft.save(update_fields=["status", "updated_at"])
            messages.success(request, "Чернетку відхилено.")
            return redirect("catalog:dish_list")
        count = int(request.POST.get("rows", "0") or 0)
        rows, errors = drafts.parse_rows(request.POST, min(count, 60))
        values = {
            "card_no": request.POST.get("card_no", "").strip(), "name": request.POST.get("name", "").strip()[:200],
            "category": request.POST.get("category", "").strip()[:100], "output_g": request.POST.get("output_g", "").strip(),
            "diets": [int(d) for d in request.POST.getlist("diets") if d.isdigit()],
            "technology": request.POST.get("technology", "").strip(),
        }
        if action != "create" or draft.status != AIDraft.Status.READY:
            return self.render(request, draft, rows, values, errors if action == "create" else [])
        card_no = values["card_no"]
        if not card_no.isdigit() or int(card_no) < 1:
            errors.append("№ картки має бути додатним числом.")
        elif Dish.objects.filter(card_no=int(card_no)).exists():
            errors.append(f"Картка №{card_no} вже існує.")
        if not values["name"]:
            errors.append("Вкажіть назву страви.")
        output = drafts.to_dec(values["output_g"], "Вихід", errors, low=drafts.D("1"))
        if errors:
            return self.render(request, draft, rows, values, errors)
        dish = drafts.create_dish(card_no=int(card_no), name=values["name"], category=values["category"],
                                  output_g=output, diets=values["diets"], technology=values["technology"],
                                  rows=rows, draft=draft)
        messages.success(request, f"Картку №{dish.card_no} створено як «Не перевірено». Перевірте норми й позначте як перевірену.")
        return redirect("catalog:dish_detail", dish.pk)

    def render(self, request, draft, rows, values, errors):
        products = list(Product.objects.filter(is_active=True).order_by("name"))
        shown = rows + [{"product": None, "brutto_g": "", "netto_g": ""} for _ in range(EXTRA_ROWS)]
        valid = [r for r in rows if r["product"] and r["netto_g"] not in (None, "")]
        for r in valid:
            r["netto_g"] = drafts.D(str(r["netto_g"]))
        dup = Dish.objects.filter(name__iexact=values["name"]).first() if values["name"] else None
        missing = draft.result.get("missing_products", [])
        return render(request, "catalog/dish_draft.html", {
            "draft": draft, "rows": shown, "values": values, "errors": errors, "products": products,
            "nutrition": drafts.rows_nutrition(valid), "diets": Diet.objects.filter(is_active=True),
            "categories": categories(), "duplicate": dup, "missing": missing,
            "missing_names": "|".join(m["name"] for m in missing),
            "warnings": draft.result.get("warnings", []),
        })


# ====================================================================== AI-06
class ProductSuggestStartView(RoleRequiredMixin, View):
    allowed_roles = CATALOG_EDITORS

    def get(self, request):
        names = "\n".join(n for n in request.GET.get("names", "").split("|") if n)
        return render(request, "catalog/product_suggest_start.html", {"names": names, "back": request.GET.get("draft", "")})

    def post(self, request):
        names = request.POST.get("names", "").splitlines()
        back = request.POST.get("back", "")
        try:
            from apps.assist.features import estimate_products

            result = estimate_products(request.user, names)
        except ValueError as exc:
            messages.error(request, str(exc))
            return render(request, "catalog/product_suggest_start.html", {"names": "\n".join(names), "back": back})
        except (client.AIUnavailable, client.AIError, client.AILimitExceeded) as exc:
            ai_error(request, exc)
            return render(request, "catalog/product_suggest_start.html", {"names": "\n".join(names), "back": back})
        parent = AIDraft.objects.filter(pk=back, kind=AIDraft.Kind.DISH).first() if back.isdigit() else None
        draft = AIDraft.objects.create(kind=AIDraft.Kind.PRODUCTS, user=request.user, input={"names": names},
                                       result=result, parent=parent)
        return redirect("catalog:product_suggest", draft.pk)


class ProductSuggestView(RoleRequiredMixin, View):
    allowed_roles = CATALOG_EDITORS

    def get(self, request, pk):
        draft = own_draft(request, pk, AIDraft.Kind.PRODUCTS)
        return render(request, "catalog/product_suggest.html", self.context(draft, []))

    def context(self, draft, errors):
        return {"draft": draft, "rows": draft.result.get("products", []), "errors": errors,
                "categories": ProductCategory.objects.all(), "model": settings.ANTHROPIC_MODEL}

    def post(self, request, pk):
        draft = own_draft(request, pk, AIDraft.Kind.PRODUCTS)
        if draft.status != AIDraft.Status.READY:
            return redirect("catalog:product_list")
        if request.POST.get("action") == "discard":
            draft.status = AIDraft.Status.DISCARDED
            draft.save(update_fields=["status", "updated_at"])
            return redirect("catalog:product_list")
        existing = {normalize(n) for n in Product.objects.values_list("name", flat=True)}
        chosen, errors = [], []
        for i, _ in enumerate(draft.result.get("products", [])):
            if request.POST.get(f"take_{i}") != "on":
                continue
            name = request.POST.get(f"name_{i}", "").strip()[:150]
            if not name:
                errors.append(f"Рядок {i + 1}: вкажіть назву.")
                continue
            if normalize(name) in existing:
                errors.append(f"«{name}» вже є в довіднику.")
                continue
            row = {"name": name, "category": request.POST.get(f"category_{i}", "")}
            for f, label in (("protein", "Білки"), ("fat", "Жири"), ("carbs", "Вуглеводи"), ("kcal", "Ккал")):
                row[f] = drafts.to_dec(request.POST.get(f"{f}_{i}"), f"{name}: {label}", errors,
                                       high=drafts.D(900) if f == "kcal" else drafts.D(100))
            existing.add(normalize(name))
            chosen.append(row)
        if not chosen and not errors:
            errors.append("Позначте хоча б один продукт.")
        if errors:
            return render(request, "catalog/product_suggest.html", self.context(draft, errors))
        created = drafts.create_products(chosen, model_name=settings.ANTHROPIC_MODEL, draft=draft)
        messages.success(request, f"Додано продуктів: {len(created)} (позначка «Оцінка ШІ», не перевірено).")
        if draft.parent_id and draft.parent.status == AIDraft.Status.READY:
            return redirect("catalog:dish_draft", draft.parent_id)
        return redirect(reverse("catalog:product_list"))
