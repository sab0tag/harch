"""S7-04: the hospital's 7-day menu cycle from Excel.

Template: sheet «Меню» — one row per dish: Дієта | День | Прийом їжі | Страва (№ картки або назва);
sheet «Картки» — every dish card for lookup. Import checks every row first (one bad row → nothing changes),
shows the week per diet, then creates one menu per diet and day with `cycle_day`, replacing that diet's
previous cycle; the day plan then picks the menu by weekday (planning.services.suggest_menu).
"""
import re
from collections import OrderedDict
from io import BytesIO

from django.db import transaction
from django.db.models import Max
from django.http import HttpResponse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from apps.catalog.models import Diet, Dish, MealSlot

from .models import CYCLE_DAYS, Menu, MenuItem

DAY_NAMES = dict(CYCLE_DAYS)
DAY_ALIASES = {"пн": 1, "вт": 2, "ср": 3, "чт": 4, "пт": 5, "сб": 6, "нд": 7, "нед": 7}
# The hospital serves four meals (docs/09_discovery.md); «Полуденок» stays importable for other hospitals.
TEMPLATE_SLOTS = (MealSlot.BREAKFAST, MealSlot.SECOND_BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER)
HEADERS = {"diet": ("дієта",), "day": ("день", "день тижня"), "slot": ("прийом їжі", "прийом"),
           "dish": ("страва", "страва (№ картки або назва)", "№ картки", "картка")}
SESSION_KEY = "menu_cycle_import"


def _norm(value):
    return re.sub(r"\s+", " ", str(value if value is not None else "").strip().lower().replace("’", "'"))


def cycle_diets():
    return Diet.objects.filter(is_active=True, has_own_menus=True).order_by("sort_order", "code")


def template_xlsx():
    wb = Workbook()
    ws = wb.active
    ws.title = "Меню"
    head = ["Дієта", "День", "Прийом їжі", "Страва (№ картки або назва)"]
    ws.append(head)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E8EFEA")
    for diet in cycle_diets():
        for day, day_name in CYCLE_DAYS:
            for slot in TEMPLATE_SLOTS:
                ws.append([diet.code, day_name, MealSlot(slot).label, ""])
    for col, width in zip("ABCD", (10, 14, 14, 46)):
        ws.column_dimensions[col].width = width
    days = DataValidation(type="list", formula1='"' + ",".join(n for _, n in CYCLE_DAYS) + '"', allow_blank=True)
    ws.add_data_validation(days)
    days.add(f"B2:B{max(ws.max_row, 2) + 500}")
    ws.freeze_panes = "A2"
    cards = wb.create_sheet("Картки")
    cards.append(["№ картки", "Страва", "Вихід, г", "Дієти"])
    for cell in cards[1]:
        cell.font = Font(bold=True)
    for dish in Dish.objects.filter(is_active=True).prefetch_related("diets").order_by("card_no"):
        cards.append([dish.card_no, dish.name, float(dish.output_g or 0), ", ".join(d.code for d in dish.diets.all())])
    cards.column_dimensions["B"].width = 46
    buf = BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = 'attachment; filename="menu-7-dniv.xlsx"'
    return resp


def _day(value):
    v = _norm(value)
    if v.isdigit() and 1 <= int(v) <= 7:
        return int(v)
    for day, name in CYCLE_DAYS:
        if v == name.lower().replace("’", "'"):
            return day
    return DAY_ALIASES.get(v[:3]) or DAY_ALIASES.get(v[:2])


def _slot(value):
    v = _norm(value)
    for slot, label in MealSlot.choices:
        if v in (slot, label.lower()):
            return slot
    return {"2 сніданок": MealSlot.SECOND_BREAKFAST, "другий сніданок": MealSlot.SECOND_BREAKFAST}.get(v)


def parse(file):
    """Rows [{row, diet_id, day, slot, dish_id}] and errors [str]; empty dish cells are skipped."""
    try:
        wb = load_workbook(file, read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 — any unreadable upload gets the same message
        return [], ["Не вдалося прочитати файл. Потрібен .xlsx (у Excel: «Зберегти як» → «Книга Excel»)."]
    ws = wb["Меню"] if "Меню" in wb.sheetnames else wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], ["Аркуш «Меню» порожній."]
    columns = {}
    for i, title in enumerate(rows[0]):
        for key, names in HEADERS.items():
            if _norm(title) in names and key not in columns:
                columns[key] = i
    if set(columns) != set(HEADERS):
        return [], ["Перший рядок має містити колонки: Дієта, День, Прийом їжі, Страва — як у шаблоні."]
    diets = {}
    for d in cycle_diets():
        diets[_norm(d.code)] = d
        diets[_norm(d.name)] = d
    dishes = {d.card_no: d for d in Dish.objects.filter(is_active=True)}
    by_name = {_norm(d.name): d for d in dishes.values()}
    result, errors = [], []
    for n, r in enumerate(rows[1:], start=2):
        cell = lambda key: r[columns[key]] if columns[key] < len(r) else None  # noqa: E731
        raw_dish = cell("dish")
        if raw_dish is None or _norm(raw_dish) == "":
            continue
        diet = diets.get(_norm(cell("diet")))
        day, slot = _day(cell("day")), _slot(cell("slot"))
        text = _norm(raw_dish)
        number = re.match(r"^№?\s*(\d+)\b", text)
        dish = dishes.get(int(number.group(1))) if number else by_name.get(text)
        problems = []
        if diet is None:
            problems.append(f"дієта «{cell('diet')}»")
        if day is None:
            problems.append(f"день «{cell('day')}»")
        if slot is None:
            problems.append(f"прийом їжі «{cell('slot')}»")
        if dish is None:
            problems.append(f"страва «{raw_dish}» (немає такої картки — спершу додайте картку)")
        if problems:
            errors.append(f"Рядок {n}: невідомо — " + "; ".join(problems))
            continue
        result.append({"row": n, "diet_id": diet.pk, "day": day, "slot": slot, "dish_id": dish.pk})
    if not result and not errors:
        errors.append("У файлі немає жодної страви.")
    return result, errors


def preview(rows):
    """{diet: {"days": {day: {slot: [dish]}}, "missing": [days], "cards_to_allow": [dish]}} in template order."""
    diets = {d.pk: d for d in cycle_diets()}
    dishes = {d.pk: d for d in Dish.objects.filter(pk__in={r["dish_id"] for r in rows}).prefetch_related("diets")}
    order = {slot: i for i, (slot, _) in enumerate(MealSlot.choices)}
    week = OrderedDict()
    for r in sorted(rows, key=lambda r: (diets[r["diet_id"]].sort_order, r["day"], order[r["slot"]], r["row"])):
        diet, dish = diets[r["diet_id"]], dishes[r["dish_id"]]
        entry = week.setdefault(diet, {"days": OrderedDict(), "cards_to_allow": []})
        entry["days"].setdefault(r["day"], OrderedDict()).setdefault(r["slot"], []).append(dish)
        if diet not in dish.diets.all() and dish not in entry["cards_to_allow"]:
            entry["cards_to_allow"].append(dish)
    for entry in week.values():
        entry["missing"] = [DAY_NAMES[d] for d, _ in CYCLE_DAYS if d not in entry["days"]]
        entry["days"] = [(DAY_NAMES[d], [(MealSlot(s).label, ds) for s, ds in slots.items()])
                         for d, slots in entry["days"].items()]
    return week


@transaction.atomic
def apply(rows, *, deactivate_others=True):
    """One menu per diet and day in the file. The diet's previous cycle is switched off (history stays),
    and with deactivate_others its other menus too; cards get the diet allowed if they lacked it."""
    diets = {d.pk: d for d in cycle_diets()}
    dishes = {d.pk: d for d in Dish.objects.filter(pk__in={r["dish_id"] for r in rows})}
    number = (Menu.objects.aggregate(m=Max("number"))["m"] or 0)
    created = []
    by_menu = OrderedDict()
    for r in rows:
        by_menu.setdefault((r["diet_id"], r["day"]), []).append(r)
    for diet_id in sorted({d for d, _ in by_menu}):
        old = Menu.objects.filter(diet_id=diet_id, is_active=True)
        (old if deactivate_others else old.filter(cycle_day__isnull=False)).update(is_active=False)
    order = {slot: i for i, (slot, _) in enumerate(MealSlot.choices)}
    for (diet_id, day), items in sorted(by_menu.items(), key=lambda kv: (diets[kv[0][0]].sort_order, kv[0][1])):
        diet = diets[diet_id]
        number += 1
        menu = Menu.objects.create(number=number, diet=diet, cycle_day=day, verified=True,
                                   title=f"{diet.code} · 7-денний цикл · {DAY_NAMES[day]}")
        for i, r in enumerate(sorted(items, key=lambda r: (order[r["slot"]], r["row"]))):
            dish = dishes[r["dish_id"]]
            if not dish.diets.filter(pk=diet_id).exists():
                dish.diets.add(diet)
            MenuItem.objects.create(menu=menu, meal_slot=r["slot"], dish=dish, sort_order=i)
        created.append(menu)
    return created


def cycle_status():
    """[(diet, {day: menu})] — what the current cycle covers, for the import page."""
    menus = Menu.objects.filter(is_active=True, cycle_day__isnull=False).select_related("diet")
    by_diet = OrderedDict((d, {}) for d in cycle_diets())
    for m in menus:
        if m.diet in by_diet:
            by_diet[m.diet][m.cycle_day] = m
    return list(by_diet.items())
