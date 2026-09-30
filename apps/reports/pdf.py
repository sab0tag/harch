"""Printable menu-rozkladka (WeasyPrint) — built only from the frozen snapshot."""
from collections import OrderedDict
from decimal import Decimal

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string


def menu_rozkladka_context(plan):
    from apps.consumption.models import ConsumptionSnapshotLine
    from apps.consumption.services import slot_label, slot_order, snapshot_totals

    lines = list(ConsumptionSnapshotLine.objects.filter(plan=plan, correction__isnull=True).order_by("id"))  # the plan as approved
    order = slot_order()
    diets = OrderedDict()
    for e in plan.entries.select_related("diet", "menu").order_by("diet__sort_order"):
        if e.menu_id:
            diets[e.diet.code] = {"code": e.diet.code, "name": e.diet.name, "menu": e.menu.number,
                                  "portions": e.portions, "slots": OrderedDict(), "kcal": Decimal(0)}
    for l in sorted(lines, key=lambda l: order.get(l.meal_slot, 99)):
        d = diets.get(l.diet_code)
        if d is None:
            continue
        d["kcal"] += l.kcal_total
        slot = d["slots"].setdefault(slot_label(l.meal_slot), OrderedDict())
        slot.setdefault(l.dish_card_no, {"card_no": l.dish_card_no, "name": l.dish_name, "output_g": l.dish_output_g})
    for d in diets.values():
        d["kcal_pp"] = (d["kcal"] / d["portions"]).quantize(Decimal("1")) if d["portions"] else None
        d["slots"] = [(label, list(dishes.values())) for label, dishes in d["slots"].items()]
    totals = snapshot_totals(plan)
    return {
        "plan": plan,
        "hospital": settings.HOSPITAL_FULL_NAME,
        "edrpou": settings.HOSPITAL_EDRPOU,
        "address": settings.HOSPITAL_ADDRESS,
        "approver_title": settings.APPROVER_TITLE,
        "diets": list(diets.values()),
        "totals": totals,
        "total_brutto": sum((b for _, b, _ in totals), Decimal(0)),
        "portions": sum(d["portions"] for d in diets.values()),
    }


def menu_rozkladka_pdf(plan):
    from weasyprint import HTML

    html = render_to_string("reports/menu_rozkladka.html", menu_rozkladka_context(plan))
    pdf = HTML(string=html).write_pdf()
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="menu-rozkladka_{plan.service_date.isoformat()}.pdf"'
    return resp


# ---------------------------------------------------------------- S7-03 бракеражний журнал
def brakerazh_context(plan):
    """Journal of finished-dish quality control for a day: every dish the kitchen cooks (corrections
    included — that is what was cooked), its portion output and kcal, with empty columns for the commission."""
    from apps.consumption.models import ConsumptionSnapshotLine
    from apps.consumption.services import kitchen_board

    kcal = {}          # (slot, card) -> kcal of one portion, from the approved plan's lines
    per_combo = {}
    for l in ConsumptionSnapshotLine.objects.filter(plan=plan, correction__isnull=True):
        key = (l.meal_slot, l.dish_card_no, l.diet_code, l.menu_number)
        total, portions = per_combo.get(key, (Decimal(0), l.portions))
        per_combo[key] = (total + l.kcal_total, portions)
    for (slot, card, _, _), (total, portions) in per_combo.items():
        if portions:
            kcal.setdefault((slot, card), (total / portions).quantize(Decimal("1")))
    slots = []
    n = 0
    for s in kitchen_board(plan):
        dishes = []
        for d in s["dishes"]:
            if d["portions"] <= 0:
                continue
            n += 1
            dishes.append({"n": n, "card_no": d["card_no"], "name": d["name"], "output_g": d["output_g"],
                           "diets": ", ".join(sorted(d["by_diet"])), "portions": d["portions"],
                           "kcal": kcal.get((s["slot"], d["card_no"]))})
        if dishes:
            slots.append({"label": s["label"], "dishes": dishes})
    return {"plan": plan, "hospital": settings.HOSPITAL_FULL_NAME, "slots": slots,
            "diets": menu_rozkladka_context(plan)["diets"]}


def brakerazh_pdf(plan):
    from weasyprint import HTML

    html = render_to_string("reports/brakerazh.html", brakerazh_context(plan))
    resp = HttpResponse(HTML(string=html).write_pdf(), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="brakerazh_{plan.service_date.isoformat()}.pdf"'
    return resp
