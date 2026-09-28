"""S5-07: «Імпорт цін з Excel» — accountant (and dietician) screens. Logic lives in price_import.py."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.accounts.access import RoleRequiredMixin
from apps.core.navigation import PRICE_EDITORS

from . import price_import as pi
from .models import PriceImport, PriceImportRow, Product


def _int_or_none(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


class PriceImportStartView(RoleRequiredMixin, View):
    allowed_roles = PRICE_EDITORS

    def get(self, request):
        return render(request, "catalog/price_import_start.html", {
            "imports": PriceImport.objects.select_related("uploaded_by", "applied_by")[:20],
        })

    def post(self, request):
        file = request.FILES.get("file")
        if file is None:
            messages.error(request, "Оберіть файл Excel (.xlsx).")
            return redirect("catalog:price_import_start")
        try:
            imp = pi.create_import(file, request.user)
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
            return redirect("catalog:price_import_start")
        return redirect("catalog:price_import", imp.pk)


class PriceImportView(RoleRequiredMixin, View):
    allowed_roles = PRICE_EDITORS

    def get(self, request, pk):
        imp = get_object_or_404(PriceImport, pk=pk)
        return render(request, "catalog/price_import.html", self.context(imp))

    def post(self, request, pk):
        imp = get_object_or_404(PriceImport, pk=pk)
        action = request.POST.get("action")
        try:
            if action == "map":
                pi.set_mapping(
                    imp, request.POST.get("sheet", ""), _int_or_none(request.POST.get("header_row")),
                    _int_or_none(request.POST.get("name_col")), _int_or_none(request.POST.get("price_col")),
                    _int_or_none(request.POST.get("unit_col")),
                    request.POST.get("unit") if request.POST.get("unit") in PriceImport.Unit.values else PriceImport.Unit.KG,
                )
            elif action == "apply":
                choices = {
                    row.pk: (request.POST.get(f"apply_{row.pk}") == "1", _int_or_none(request.POST.get(f"product_{row.pk}")))
                    for row in imp.rows.all()
                }
                changed = pi.apply(imp, request.user, choices)
                messages.success(request, f"Ціни оновлено: {changed}.")
            elif action == "cancel":
                pi.cancel(imp)
                messages.info(request, "Імпорт скасовано, ціни не змінено.")
                return redirect("catalog:price_import_start")
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        return redirect("catalog:price_import", imp.pk)

    def context(self, imp):
        rows = list(imp.rows.select_related("product"))
        sheet_rows = imp.sheets.get(imp.sheet, [])
        width = len(sheet_rows[0]) if sheet_rows else 0
        start = imp.header_row if imp.header_row is not None else 0
        for r in rows:
            pct = r.change_pct
            r.pct = pct
            r.big_change = pct is not None and abs(pct) > pi.BIG_CHANGE_PCT
        return {
            "imp": imp,
            "rows": rows,
            "columns": [(i, pi.column_letter(i), sheet_rows[imp.header_row][i] if imp.header_row is not None else "")
                        for i in range(width)],
            "header_choices": [(i, " · ".join(c for c in row if c)[:80]) for i, row in enumerate(sheet_rows[:pi.HEADER_SCAN_ROWS])],
            "preview": [(i + 1, row) for i, row in enumerate(sheet_rows[start:start + pi.PREVIEW_ROWS + 1], start=start)],
            "units": PriceImport.Unit.choices,
            "products": Product.objects.filter(is_active=True).only("pk", "name", "price_per_kg"),
            "counts": {
                "total": len(rows),
                "selected": sum(r.selected for r in rows),
                "fuzzy": sum(r.match == PriceImportRow.Match.FUZZY for r in rows),
                "none": sum(r.match == PriceImportRow.Match.NONE for r in rows),
                "problem": sum(bool(r.problem) for r in rows),
            },
            "editable": imp.status == PriceImport.Status.DRAFT,
        }
