"""S7-02: the head of the food store sees what to issue for a day — plan plus corrections — and nothing else."""
from io import StringIO

import pytest
from django.core.management import call_command

from apps.accounts.models import Role, User
from apps.accounts.tests.factories import UserFactory
from apps.consumption.tests.conftest import cooking_day, diet, world  # noqa: F401
from apps.orders import corrections
from apps.orders.tests.conftest import DAY, as_user
from apps.workflow import services as wf

pytestmark = pytest.mark.django_db


@pytest.fixture
def store(cooking_day):
    cooking_day.store = UserFactory(role=Role.STOREKEEPER)
    return cooking_day


def test_store_sees_the_days_issue_with_corrections(client, store):
    w = store
    page = as_user(client, w.store).get(f"/store/{DAY.isoformat()}/")
    html = page.content.decode()
    assert page.status_code == 200 and "Разом, кг" in html and "Коригування відділень" not in html
    rows = {r["name"]: r["planned_g"] for r in page.context["data"]["rows"]}
    c = corrections.open_correction(w.depts["Терапевтичне"], DAY, w.nurse["Терапевтичне"])
    corrections.submit_correction(c, w.nurse["Терапевтичне"], {diet("ОВД").pk: 2}, from_meal="lunch", reason="admission")
    wf.approve(c.task, w.dietician)
    page = as_user(client, w.store).get(f"/store/{DAY.isoformat()}/")
    after = {r["name"]: r["planned_g"] for r in page.context["data"]["rows"]}
    assert "Коригування відділень" in page.content.decode()
    assert sum(after.values()) > sum(rows.values())                    # the store issues for the extra patients
    assert as_user(client, w.store).get(f"/reports/menu-vymoha/{DAY.isoformat()}.xlsx").status_code == 200


def test_store_before_the_plan_is_approved(client, world):
    store = UserFactory(role=Role.STOREKEEPER)
    html = as_user(client, store).get("/store/2026-10-02/").content.decode()
    assert "ще не затверджено" in html


@pytest.mark.parametrize("url, code", [("/orders/", 403), ("/kitchen/", 403), ("/write-off/", 403),
                                       ("/catalog/products/", 200), ("/", 200), ("/help/", 200)])
def test_store_sees_only_its_own_screens(client, store, url, code):
    assert as_user(client, store.store).get(url).status_code == code


def test_store_in_menu_and_home(client, store):
    html = as_user(client, store.store).get("/").content.decode()
    assert 'data-nav="Видача зі складу"' in html and "на сьогодні" in html
    assert 'data-nav="Замовлення"' not in html


def test_staff_import_knows_the_storekeeper(tmp_path, world):
    path = tmp_path / "staff.csv"
    path.write_text("ПІБ;роль\nГрищенко Ніна;Комірник\n", encoding="utf-8")
    call_command("create_pilot_users", str(path), stdout=StringIO())
    assert User.objects.get(last_name="Грищенко").role == Role.STOREKEEPER
