"""Freeze the consumption of an approved plan (CLAUDE.md invariant 5)."""
from collections import OrderedDict
from decimal import Decimal

from django.db import transaction

from .calc import PlanInput, compute_lines
from .models import ConsumptionSnapshotLine


class AlreadyFrozen(Exception):
    pass


@transaction.atomic
def freeze(plan):
    if ConsumptionSnapshotLine.objects.filter(plan=plan).exists():
        raise AlreadyFrozen(f"Розхід для {plan} уже зафіксовано")
    entries = [PlanInput(e.menu.number, e.portions) for e in plan.entries.select_related("menu") if e.menu_id]
    lines = compute_lines(entries)
    ConsumptionSnapshotLine.objects.bulk_create([
        ConsumptionSnapshotLine(
            plan=plan,
            diet_code=line.diet_code,
            menu_number=line.menu_number,
            meal_slot=line.meal_slot,
            dish_card_no=line.dish_card_no,
            dish_name=line.dish_name,
            product_id=line.product_id,
            product_name=line.product_name,
            portions=line.portions,
            brutto_per_portion_g=line.brutto_pp,
            netto_per_portion_g=line.netto_pp,
            brutto_total_g=line.brutto_total,
            netto_total_g=line.netto_total,
            kcal_total=line.kcal_total.quantize(Decimal("0.01")),
        )
        for line in lines
    ])
    return len(lines)


def snapshot_totals(plan):
    """[(product_name, brutto_g, netto_g)] from the frozen snapshot, by product name."""
    acc = OrderedDict()
    for row in ConsumptionSnapshotLine.objects.filter(plan=plan).order_by("product_name").values(
        "product_name", "brutto_total_g", "netto_total_g"
    ):
        b, n = acc.get(row["product_name"], (Decimal(0), Decimal(0)))
        acc[row["product_name"]] = (b + row["brutto_total_g"], n + row["netto_total_g"])
    return [(name, b, n) for name, (b, n) in acc.items()]
