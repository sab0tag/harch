"""XLSX exports (openpyxl). Numbers are written as numbers (kg with 3 decimals) so accountants can sum."""
import calendar
from collections import OrderedDict
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.conf import settings
from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from apps.core.text import uk_sort_key

KG = Decimal(1000)
BOLD = Font(bold=True)
HEAD_FILL = PatternFill("solid", fgColor="E8EFEA")
THIN = Side(style="thin", color="BBBBBB")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
KG_FMT = "#,##0.000"
MONEY_FMT = "#,##0.00"


def _kg(grams):
    return float((Decimal(grams) / KG).quantize(Decimal("0.001")))


def _response(wb, filename):
    buf = BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def _header(ws, row, titles, widths=None):
    for col, title in enumerate(titles, 1):
        c = ws.cell(row=row, column=col, value=title)
        c.font, c.fill, c.border = BOLD, HEAD_FILL, BOX
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for col, width in enumerate(widths or [], 1):
        ws.column_dimensions[get_column_letter(col)].width = width


def consumption_xlsx(data, d_from, d_to, group):
    from apps.consumption.services import GROUPS

    wb = Workbook()
    ws = wb.active
    ws.title = "Розхід"
    period = d_from.strftime("%d.%m.%Y") + ("" if d_from == d_to else " – " + d_to.strftime("%d.%m.%Y"))
    ws["A1"] = settings.HOSPITAL_NAME
    ws["A2"] = f"Розхід продуктів {period} ({GROUPS[group]})"
    ws["A2"].font = Font(bold=True, size=13)
    ws["A3"] = "Джерело: зафіксований розхід затверджених планів. Сума — за поточними цінами довідника."
    _header(ws, 5, ["Продукт", "Брутто, кг", "Нетто, кг", "Ціна за кг, грн", "Сума, грн"], [36, 14, 14, 16, 16])
    r = 6
    for g in data["groups"]:
        if g["label"]:
            ws.cell(row=r, column=1, value=g["label"]).font = BOLD
            r += 1
        for row in g["rows"]:
            ws.cell(row=r, column=1, value=row["product_name"])
            ws.cell(row=r, column=2, value=_kg(row["brutto_g"])).number_format = KG_FMT
            ws.cell(row=r, column=3, value=_kg(row["netto_g"])).number_format = KG_FMT
            if row["price_per_kg"] is not None:
                ws.cell(row=r, column=4, value=float(row["price_per_kg"])).number_format = MONEY_FMT
                ws.cell(row=r, column=5, value=float(row["cost"])).number_format = MONEY_FMT
            r += 1
        if g["label"]:
            ws.cell(row=r, column=1, value=f"Разом: {g['label']}").font = BOLD
            ws.cell(row=r, column=2, value=_kg(g["brutto_g"])).number_format = KG_FMT
            ws.cell(row=r, column=5, value=float(g["cost"])).number_format = MONEY_FMT
            r += 1
    ws.cell(row=r, column=1, value="РАЗОМ").font = BOLD
    ws.cell(row=r, column=2, value=_kg(data["brutto_g"])).number_format = KG_FMT
    c = ws.cell(row=r, column=5, value=float(data["cost"]))
    c.number_format, c.font = MONEY_FMT, BOLD
    ws.freeze_panes = "A6"
    return _response(wb, f"rozkhid_{d_from.isoformat()}_{d_to.isoformat()}.xlsx")


def accumulative_data(year, month):
    """Product × day of month, kg, from APPROVED write-offs (the accounting source)."""
    from apps.consumption.models import WriteOffLine

    days_in_month = calendar.monthrange(year, month)[1]
    lines = WriteOffLine.objects.filter(
        write_off__approved_at__isnull=False,
        write_off__plan__service_date__year=year, write_off__plan__service_date__month=month,
    ).values_list("product_id", "product_name", "write_off__plan__service_date", "quantity_g", "cost")
    products, closed_days = OrderedDict(), set()
    for pid, name, day, qty, cost in sorted(lines, key=lambda l: (uk_sort_key(l[1]), l[2])):
        p = products.setdefault(pid, {"name": name, "days": {}, "total_g": Decimal(0), "cost": Decimal(0)})
        p["days"][day.day] = p["days"].get(day.day, Decimal(0)) + qty
        p["total_g"] += qty
        p["cost"] += cost
        closed_days.add(day.day)
    for p in products.values():
        # Prices can change within a month; the form shows the weighted average.
        p["avg_price"] = (p["cost"] / p["total_g"] * KG).quantize(Decimal("0.01")) if p["total_g"] else None
    return {"days": days_in_month, "products": list(products.values()), "closed_days": closed_days,
            "cost": sum((p["cost"] for p in products.values()), Decimal(0))}


MONTHS = ["", "січень", "лютий", "березень", "квітень", "травень", "червень", "липень", "серпень",
          "вересень", "жовтень", "листопад", "грудень"]


def _form_header(ws, form_no, last_col):
    ws["A1"] = settings.HOSPITAL_NAME
    ws["A1"].font = BOLD
    c = ws.cell(row=1, column=last_col, value=f"Типова форма № {form_no}")
    c.alignment = Alignment(horizontal="right")


def _signatures(ws, row, labels):
    for label in labels:
        ws.cell(row=row, column=2, value=f"{label}  ____________  ______________________")
        row += 2
    return row


def accumulative_xlsx(year, month):
    """Накопичувальна відомість витрачання продуктів харчування, typical form З-13 layout."""
    data = accumulative_data(year, month)
    n = data["days"]
    total_col = 4 + n              # № | name | unit | days… | total | price | sum
    wb = Workbook()
    ws = wb.active
    ws.title = f"{month:02d}.{year}"
    _form_header(ws, "З-13", total_col + 2)
    ws["A3"] = f"НАКОПИЧУВАЛЬНА ВІДОМІСТЬ витрачання продуктів харчування за {MONTHS[month]} {year} р."
    ws["A3"].font = Font(bold=True, size=13)
    open_days = [d for d in range(1, n + 1) if d not in data["closed_days"]]
    ws["A4"] = ("Дні без затвердженого списання: " + ", ".join(map(str, open_days))) if open_days else \
        "Усі дні місяця мають затверджене списання."
    titles = ["№ з/п", "Найменування продуктів харчування", "Одиниця виміру"] + \
        [str(d) for d in range(1, n + 1)] + ["Разом, кількість", "Ціна, грн", "Сума, грн"]
    _header(ws, 6, titles, [6, 30, 9] + [7] * n + [12, 11, 14])
    r = 7
    for i, p in enumerate(data["products"], 1):
        ws.cell(row=r, column=1, value=i).border = BOX
        ws.cell(row=r, column=2, value=p["name"]).border = BOX
        ws.cell(row=r, column=3, value="кг").border = BOX
        for d in range(1, n + 1):
            c = ws.cell(row=r, column=3 + d, value=_kg(p["days"][d]) if d in p["days"] else None)
            c.number_format, c.border = KG_FMT, BOX
        c = ws.cell(row=r, column=total_col, value=_kg(p["total_g"]))
        c.number_format, c.font, c.border = KG_FMT, BOLD, BOX
        c = ws.cell(row=r, column=total_col + 1, value=float(p["avg_price"]) if p["avg_price"] else None)
        c.number_format, c.border = MONEY_FMT, BOX
        c = ws.cell(row=r, column=total_col + 2, value=float(p["cost"]))
        c.number_format, c.border = MONEY_FMT, BOX
        r += 1
    ws.cell(row=r, column=2, value="РАЗОМ").font = BOLD
    c = ws.cell(row=r, column=total_col + 2, value=float(data["cost"]))
    c.number_format, c.font = MONEY_FMT, BOLD
    _signatures(ws, r + 3, ("Бухгалтер", "Лікар-дієтолог", "Завідувач виробництва"))
    ws.freeze_panes = "D7"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    return _response(wb, f"nakopychuvalna_{year}-{month:02d}.xlsx")


# ====================================================================== S4-05 меню-вимога З-4а
def menu_vymoha_data(plan):
    """Daily issue of products for a frozen plan, typical form З-4а.

    Planned quantities by meal come from the snapshot (what was approved). «Видано» and prices come
    from the write-off once it is prepared, else from the kitchen fact / current catalog prices.
    """
    from apps.catalog.models import MealSlot, Product
    from apps.consumption.models import ActualConsumption, ConsumptionSnapshotLine
    from apps.consumption.services import cost_of

    snapshot = ConsumptionSnapshotLine.objects.filter(plan=plan)
    eaters = OrderedDict()
    for code, menu_no, portions in snapshot.order_by("diet_code").values_list(
            "diet_code", "menu_number", "portions").distinct():
        eaters[code] = portions
    slots_used = set(snapshot.values_list("meal_slot", flat=True))
    slots = [(v, label) for v, label in MealSlot.choices if v in slots_used]

    products = OrderedDict()
    for pid, name, slot, grams in snapshot.values_list("product_id", "product_name", "meal_slot", "brutto_total_g"):
        p = products.setdefault(pid, {"name": name, "by_slot": {}, "planned_g": Decimal(0)})
        p["by_slot"][slot] = p["by_slot"].get(slot, Decimal(0)) + grams
        p["planned_g"] += grams

    wo = getattr(plan, "write_off", None)     # reverse one-to-one: None until the accountant opens it
    lines = {l.product_id: l for l in wo.lines.all()} if wo is not None and wo.prepared_at else {}
    if lines:
        issued = {pid: (l.product_name, l.quantity_g, l.price_per_kg) for pid, l in lines.items()}
        source = "approved" if wo.approved_at else "submitted"
    else:
        facts = {a.product_id: a for a in ActualConsumption.objects.filter(plan=plan)}
        prices = dict(Product.objects.filter(pk__in=set(products) | set(facts)).values_list("pk", "price_per_kg"))
        issued = {}
        for pid in set(products) | set(facts):
            a = facts.get(pid)
            name = a.product_name if a else products[pid]["name"]
            issued[pid] = (name, a.actual_brutto_g if a else products[pid]["planned_g"], prices.get(pid))
        source = "fact" if facts else "plan"
    for pid, (name, qty, price) in issued.items():   # replacements exist only in the fact
        products.setdefault(pid, {"name": name, "by_slot": {}, "planned_g": Decimal(0)})

    rows = []
    for pid, p in sorted(products.items(), key=lambda kv: uk_sort_key(kv[1]["name"])):
        _, qty, price = issued.get(pid, (p["name"], Decimal(0), None))
        if not qty and not p["planned_g"]:
            continue
        rows.append({**p, "issued_g": qty, "price_per_kg": price, "cost": cost_of(qty, price)})
    return {
        "plan": plan, "eaters": eaters, "eaters_total": sum(eaters.values()), "slots": slots, "rows": rows,
        "cost": sum((r["cost"] or 0 for r in rows), Decimal(0)), "source": source,
        "missing_prices": any(r["price_per_kg"] is None for r in rows),
    }


VYMOHA_SOURCE = {
    "approved": "«Видано» та ціни — із затвердженого списання.",
    "submitted": "«Видано» та ціни — зі списання, поданого на перевірку (ще не затверджено).",
    "fact": "Попередньо: «Видано» — факт кухні, ціни — поточні з довідника (списання ще не подано).",
    "plan": "Попередньо: «Видано» = план, ціни — поточні з довідника (факт кухні ще не внесено).",
}


def menu_vymoha_xlsx(plan):
    data = menu_vymoha_data(plan)
    slots = data["slots"]
    first_slot, total_col = 4, 4 + len(slots)      # № | name | unit | slots… | total | issued | price | sum
    last_col = total_col + 3
    wb = Workbook()
    ws = wb.active
    ws.title = "Меню-вимога"
    _form_header(ws, "З-4а", last_col)
    ws.cell(row=2, column=last_col, value="ЗАТВЕРДЖУЮ").alignment = Alignment(horizontal="right")
    ws.cell(row=3, column=last_col, value="Головний лікар ____________").alignment = Alignment(horizontal="right")
    day = plan.service_date
    ws["A5"] = f"МЕНЮ-ВИМОГА № {day:%j} на видачу продуктів харчування на {day:%d.%m.%Y} р."
    ws["A5"].font = Font(bold=True, size=13)
    ws["A6"] = VYMOHA_SOURCE[data["source"]]

    ws["A8"] = "Кількість осіб, що харчуються:"
    ws["A8"].font = BOLD
    col = 2
    for code, portions in data["eaters"].items():
        ws.cell(row=8, column=col, value=f"{code}: {portions}")
        col += 1
    ws.cell(row=8, column=col, value=f"Разом: {data['eaters_total']}").font = BOLD

    head = 10
    for c, title in ((1, "№ з/п"), (2, "Найменування продуктів харчування"), (3, "Одиниця виміру"),
                     (total_col, "Разом до закладки"), (total_col + 1, "Видано (фактично)"),
                     (total_col + 2, "Ціна за кг, грн"), (total_col + 3, "Сума, грн")):
        ws.merge_cells(start_row=head, start_column=c, end_row=head + 1, end_column=c)
        ws.cell(row=head, column=c, value=title)
    if slots:
        ws.merge_cells(start_row=head, start_column=first_slot, end_row=head, end_column=total_col - 1)
        ws.cell(row=head, column=first_slot, value="Кількість продуктів, що підлягають закладці")
    for i, (_, label) in enumerate(slots):
        ws.cell(row=head + 1, column=first_slot + i, value=label)
    for r in (head, head + 1):
        for c in range(1, last_col + 1):
            cell = ws.cell(row=r, column=c)
            cell.font, cell.fill, cell.border = BOLD, HEAD_FILL, BOX
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for c, width in enumerate([6, 32, 9] + [11] * len(slots) + [12, 12, 11, 13], 1):
        ws.column_dimensions[get_column_letter(c)].width = width

    r = head + 2
    for i, row in enumerate(data["rows"], 1):
        values = [i, row["name"], "кг"] + [_kg(row["by_slot"][v]) if v in row["by_slot"] else None for v, _ in slots]
        values += [_kg(row["planned_g"]), _kg(row["issued_g"]),
                   float(row["price_per_kg"]) if row["price_per_kg"] is not None else None,
                   float(row["cost"]) if row["cost"] is not None else None]
        for c, value in enumerate(values, 1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.border = BOX
            if c >= first_slot:
                cell.number_format = MONEY_FMT if c >= total_col + 2 else KG_FMT
        r += 1
    ws.cell(row=r, column=2, value="РАЗОМ").font = BOLD
    c = ws.cell(row=r, column=last_col, value=float(data["cost"]))
    c.number_format, c.font = MONEY_FMT, BOLD
    if data["missing_prices"]:
        ws.cell(row=r + 1, column=2, value="Увага: не для всіх продуктів вказано ціну.")
    _signatures(ws, r + 3, ("Склала: сестра медична з дієтичного харчування", "Лікар-дієтолог", "Бухгалтер",
                            "Завідувач виробництва", "Продукти видав (комірник)", "Продукти одержав (кухар)"))
    ws.freeze_panes = ws.cell(row=head + 2, column=3)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    return _response(wb, f"menu-vymoha_{day.isoformat()}.xlsx")
