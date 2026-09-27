"""S4-01: kitchen board, card scaled to N portions, fact table → cooking task."""
from decimal import Decimal

import pytest

from apps.catalog.models import DishIngredient, Product
from apps.consumption import services
from apps.consumption.models import ActualConsumption, ConsumptionSnapshotLine
from apps.consumption.tests.conftest import DAY, as_user
from apps.workflow.definitions import WRITE_OFF
from apps.workflow.models import Task

pytestmark = pytest.mark.django_db
URL = f"/kitchen/{DAY.isoformat()}/"


def test_board_portions_match_plan(cooking_day):
    board = services.kitchen_board(cooking_day.plan)
    assert [s["label"] for s in board][0] == "Сніданок"
    by_diet = {e.diet.code: e.portions for e in cooking_day.plan.entries.all()}
    for s in board:
        for d in s["dishes"]:
            assert d["portions"] == sum(d["by_diet"].values())
            assert all(by_diet[c] == p for c, p in d["by_diet"].items())


def test_scaled_card_multiplies_frozen_norms(cooking_day):
    line = ConsumptionSnapshotLine.objects.filter(plan=cooking_day.plan).first()
    card = services.scaled_card(cooking_day.plan, line.meal_slot, line.dish_card_no, 10)
    row = next(r for r in card["rows"] if r["product"] == line.product_name)
    assert row["brutto"] == line.brutto_per_portion_g * 10 and card["portions"] == 10


def test_card_on_kitchen_ignores_later_card_edits(client, cooking_day):
    line = ConsumptionSnapshotLine.objects.filter(plan=cooking_day.plan).first()
    DishIngredient.objects.filter(dish__card_no=line.dish_card_no).update(brutto_g=Decimal("999"))
    card = services.scaled_card(cooking_day.plan, line.meal_slot, line.dish_card_no, 1)
    assert all(r["brutto_pp"] != Decimal("999") for r in card["rows"])


def test_kitchen_page_and_dish_card(client, cooking_day):
    c = as_user(client, cooking_day.chef)
    html = c.get(URL).content.decode()
    assert "Фактичний розхід" in html and "Подати дієтсестрі" in html
    line = ConsumptionSnapshotLine.objects.filter(plan=cooking_day.plan).first()
    r = c.get(f"{URL}dish/{line.meal_slot}/{line.dish_card_no}/?n=7", HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and 'value="7"' in r.content.decode()


def test_fact_starts_equal_to_plan(client, cooking_day):
    as_user(client, cooking_day.chef).get(URL)
    totals = services.snapshot_by_product(cooking_day.plan)
    actuals = {a.product_id: a for a in ActualConsumption.objects.filter(plan=cooking_day.plan)}
    assert set(actuals) == set(totals)
    assert all(a.actual_brutto_g == a.planned_brutto_g == totals[pid][1] for pid, a in actuals.items())


def fact_form(plan, changes=None, reasons=None):
    data = {}
    for a in ActualConsumption.objects.filter(plan=plan):
        grams = (changes or {}).get(a.product_name, a.actual_brutto_g)
        data[f"fact_{a.pk}"] = f"{Decimal(grams) / 1000:.3f}".replace(".", ",")  # comma, as typed on a phone
        data[f"reason_{a.pk}"] = (reasons or {}).get(a.product_name, "")
    return data


def test_big_deviation_needs_reason_then_submits(client, cooking_day):
    c = as_user(client, cooking_day.chef)
    c.get(URL)
    a = ActualConsumption.objects.filter(plan=cooking_day.plan).order_by("-planned_brutto_g").first()
    more = a.planned_brutto_g * Decimal("1.5")
    r = c.post(URL, {**fact_form(cooking_day.plan, {a.product_name: more}), "action": "submit"}, follow=True)
    assert "вкажіть причину" in r.content.decode()
    cooking_day.cooking.refresh_from_db()
    assert cooking_day.cooking.status != "submitted"
    a.refresh_from_db()
    assert a.actual_brutto_g == more.quantize(Decimal("0.01"))  # saved even though submit was refused

    c.post(URL, {**fact_form(cooking_day.plan, reasons={a.product_name: "пригоріло, варили повторно"}),
                 "action": "submit"})
    cooking_day.cooking.refresh_from_db()
    assert cooking_day.cooking.status == "submitted"
    # locked after submit
    c.post(URL, {**fact_form(cooking_day.plan, {a.product_name: 1}), "action": "save"})
    a.refresh_from_db()
    assert a.actual_brutto_g == more.quantize(Decimal("0.01"))


def test_replacement_product_is_added(client, cooking_day):
    c = as_user(client, cooking_day.chef)
    c.get(URL)
    used = ActualConsumption.objects.filter(plan=cooking_day.plan).values_list("product_id", flat=True)
    product = Product.objects.exclude(pk__in=used).first()
    c.post(URL, {**fact_form(cooking_day.plan), "extra_product": product.pk, "extra_kg": "1,5",
                 "extra_reason": "замість судака", "action": "save"})
    a = ActualConsumption.objects.get(plan=cooking_day.plan, product=product)
    assert (a.planned_brutto_g, a.actual_brutto_g, a.reason) == (0, Decimal("1500.00"), "замість судака")


def test_dietician_approves_fact_and_write_off_is_created(client, cooking_day):
    c = as_user(client, cooking_day.chef)
    c.get(URL)
    c.post(URL, {**fact_form(cooking_day.plan), "action": "submit"})
    r = as_user(client, cooking_day.dietician).post(
        f"/tasks/{cooking_day.cooking.pk}/approve/", {"next": URL}, follow=True)
    assert "Затверджено" in r.content.decode()
    assert Task.objects.filter(type=WRITE_OFF, service_date=DAY).exists()


def test_negative_or_garbage_fact_rejected(client, cooking_day):
    c = as_user(client, cooking_day.chef)
    c.get(URL)
    data = fact_form(cooking_day.plan)
    key = next(k for k in data if k.startswith("fact_"))
    data[key] = "-2"
    assert "кількість не може бути" in c.post(URL, data, follow=True).content.decode()
    data[key] = "abc"
    assert "в кілограмах" in c.post(URL, data, follow=True).content.decode()


def test_kitchen_access(client, cooking_day):
    assert as_user(client, cooking_day.nurse["Терапевтичне"]).get(URL).status_code == 403
    assert as_user(client, cooking_day.accountant).get(URL).status_code == 403
    html = as_user(client, cooking_day.dietician).get(URL).content.decode()
    assert "Подати дієтсестрі" not in html   # dietician sees the kitchen but the chef fills it


def test_kitchen_before_plan_approval(client, world):
    html = as_user(client, world.chef).get(URL).content.decode()
    assert "ще не затверджено" in html
