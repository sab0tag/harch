"""S1-04: menu screens — nutrition vs diet norm."""
import re

import pytest

from apps.accounts.models import Role

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("role,status", [
    (Role.ADMIN, 200), (Role.DIETICIAN, 200), (Role.CHEF, 200),
    (Role.ACCOUNTANT, 403), (Role.DEPT_NURSE, 403),
])
def test_menu_access(seeded, login, role, status):
    client = login(role)
    assert client.get("/menus/").status_code == status
    assert client.get("/menus/1/").status_code == status


def test_list_summary_counts_cover_all_menus(seeded, login):
    response = login(Role.DIETICIAN).get("/menus/")
    s = response.context["summary"]
    assert s["ok"] + s["warn"] + s["bad"] == 250


def test_filter_by_diet_and_status(seeded, login):
    client = login(Role.DIETICIAN)
    page = client.get("/menus/?diet=НКД").context["page"]
    assert page.paginator.count == 20
    assert {m.diet.code for m in page} == {"НКД"}
    bad = client.get("/menus/?status=bad").context["page"]
    assert all(m.kcal_status == "bad" for m in bad)


def test_detail_shows_day_totals_vs_norm(seeded, login):
    response = login(Role.DIETICIAN).get("/menus/1/")
    norms = {r["field"]: r for r in response.context["norms"]}
    assert str(norms["kcal"]["value"]) == "1980.8"
    assert norms["kcal"]["status"] == "warn"   # 1980.8 vs 2170–2400, within 10%
    html = response.content.decode()
    for slot in ("Сніданок", "2-й сніданок", "Обід", "Полуденок", "Вечеря"):
        assert slot in html
    assert re.search(r"норма 2\s?170–2\s?400", html)


def test_detail_links_to_neighbour_menus_of_same_diet(seeded, login):
    html = login(Role.DIETICIAN).get("/menus/80/").content.decode()
    assert "/menus/79/" in html and "/menus/81/" not in html  # 81 is ЩД


def test_unknown_menu_404(seeded, login):
    assert login(Role.DIETICIAN).get("/menus/9999/").status_code == 404
