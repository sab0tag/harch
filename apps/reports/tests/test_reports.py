"""S4-04: printable menu-rozkladka, accumulative statement, and the full day end to end."""
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import load_workbook

from apps.catalog.models import Product
from apps.consumption.models import WriteOff
from apps.consumption.tests.conftest import DAY, ORDERS, as_user, cooking_day, diet, write_off_day, world  # noqa: F401
from apps.reports.pdf import menu_rozkladka_context
from apps.workflow.definitions import COOKING, MENU_PLANNING, ORDER_SUBMISSION, WRITE_OFF
from apps.workflow.models import Notification, Task

pytestmark = pytest.mark.django_db


def test_menu_pdf_for_approved_plan(client, cooking_day):
    r = as_user(client, cooking_day.chef).get(f"/reports/menu/{DAY.isoformat()}.pdf")
    assert r["Content-Type"] == "application/pdf" and r.content[:4] == b"%PDF"


def test_menu_pdf_context_matches_plan(cooking_day):
    ctx = menu_rozkladka_context(cooking_day.plan)
    assert {d["code"]: d["portions"] for d in ctx["diets"]} == {"ОВД": 32, "ЩД": 20, "№9": 8}
    assert ctx["portions"] == 60 and all(d["slots"] for d in ctx["diets"])


def test_menu_pdf_refused_before_approval(client, world):
    r = as_user(client, world.dietician).get(f"/reports/menu/{DAY.isoformat()}.pdf")
    assert r.status_code == 302 and "/plan/" in r.url


def test_menu_pdf_access(client, cooking_day):
    assert as_user(client, cooking_day.nurse["Терапевтичне"]).get(f"/reports/menu/{DAY.isoformat()}.pdf").status_code == 403


def test_accumulative_statement_from_approved_write_offs(client, write_off_day):
    w = write_off_day
    Product.objects.update(price_per_kg=Decimal("20.00"))
    as_user(client, w.accountant).post(f"/write-off/{DAY.isoformat()}/", {"action": "submit"})
    month = DAY.strftime("%Y-%m")
    c = as_user(client, w.accountant)
    assert "ще немає затверджених" in c.get(f"/reports/accumulative/{month}/").content.decode()  # submitted ≠ approved
    as_user(client, w.dietician).post(f"/tasks/{w.write_off.pk}/approve/")
    r = as_user(client, w.accountant).get(f"/reports/accumulative/{month}/?format=xlsx")
    ws = load_workbook(BytesIO(r.content)).active
    wo = WriteOff.objects.get(plan=w.plan)
    line = wo.lines.order_by("product_name").first()
    row = next(i for i in range(6, ws.max_row + 1) if ws.cell(row=i, column=1).value == line.product_name)
    assert ws.cell(row=row, column=1 + DAY.day).value == pytest.approx(float(line.quantity_g / 1000), abs=0.001)
    assert ws.cell(row=row, column=1 + DAY.day + 1).value is None           # next day has no write-off


def test_reports_access(client, world):
    assert as_user(client, world.chef).get("/reports/").status_code == 403
    assert as_user(client, world.accountant).get("/reports/").status_code == 200


def test_full_day_end_to_end(client, world):
    """Orders from 3 departments → plan → chef → kitchen fact → write-off closed. All through HTTP."""
    day = DAY.isoformat()
    for name, portions in ORDERS.items():
        c = as_user(client, world.nurse[name])
        url = f"/orders/{world.depts[name].pk}/{day}/"
        c.get(url)
        c.post(url, {"action": "submit", **{f"diet_{diet(k).pk}": v for k, v in portions.items()}})
    c = as_user(client, world.dietician)
    c.post("/orders/summary/", {"date": day, "action": "approve_all"})
    plan_task = Task.objects.get(type=MENU_PLANNING, service_date=DAY)
    html = c.get(f"/plan/{day}/").content.decode()
    assert "Подати шеф-кухарю" in html
    from apps.planning.models import DailyPlan

    plan = DailyPlan.objects.get(service_date=DAY)
    c.post(f"/plan/{day}/", {"action": "submit", **{f"menu_{e.diet_id}": e.menu_id for e in plan.entries.all()}})
    as_user(client, world.chef).post(f"/tasks/{plan_task.pk}/approve/")

    chef = as_user(client, world.chef)
    chef.get(f"/kitchen/{day}/")
    fact = {}
    for a in plan.actuals.all():
        fact[f"fact_{a.pk}"] = f"{a.actual_brutto_g / 1000:.3f}"
    chef.post(f"/kitchen/{day}/", {**fact, "action": "submit"})
    cooking = Task.objects.get(type=COOKING, service_date=DAY)
    as_user(client, world.dietician).post(f"/tasks/{cooking.pk}/approve/")

    Product.objects.update(price_per_kg=Decimal("55.00"))
    as_user(client, world.accountant).post(f"/write-off/{day}/", {"action": "submit"})
    write_off = Task.objects.get(type=WRITE_OFF, service_date=DAY)
    as_user(client, world.dietician).post(f"/tasks/{write_off.pk}/approve/")

    statuses = dict(Task.objects.filter(service_date=DAY).values_list("type", "status").distinct())
    assert set(Task.objects.filter(service_date=DAY).values_list("status", flat=True)) == {"approved"}
    assert statuses.keys() == {ORDER_SUBMISSION, MENU_PLANNING, COOKING, WRITE_OFF}
    wo = WriteOff.objects.get(plan=plan)
    assert wo.approved_at and wo.total_cost == sum(l.cost for l in wo.lines.all()) > 0
    # the accountant was told the write-off was approved
    assert Notification.objects.filter(user=world.accountant, task=write_off, title__startswith="Затверджено").exists()
