"""AI-08: menu combinations under the diet norm — optimisation in code, AI only comments."""
import json

import pytest

from apps.accounts.models import Role
from apps.assist import client
from apps.assist.models import AIRequestLog
from apps.catalog.models import Diet, Dish
from apps.menus.composer import Composer, save_new_menu, validate_items
from apps.menus.models import Menu

pytestmark = pytest.mark.django_db


def ovd():
    return Diet.objects.get(code="ОВД")


def test_fit_reaches_norm_with_same_category_swaps(seeded):
    c = Composer(ovd())
    menu = Menu.objects.get(number=1)
    base, options = c.fit_options(menu)
    assert not c.in_norm(base)
    assert any(c.in_norm(o) for o in options)
    allowed = set(ovd().dishes.values_list("pk", flat=True))
    for o in options:
        assert all(d.pk in allowed for d in o.dishes)
        for kind, _, old, new in o.changes:
            if kind == "swap":
                assert old.category == new.category


def test_generated_menus_are_in_norm_distinct_and_valid(seeded):
    c = Composer(ovd())
    options = c.generate(count=3, seed=7)
    assert len(options) == 3 and all(c.in_norm(o) for o in options)
    sets = [{d.pk for d in o.dishes} for o in options]
    assert sets[0] != sets[1] != sets[2]
    for o in options:
        validate_items(ovd(), c.describe(o)["items"])  # raises if anything is not allowed


def test_generation_is_reproducible(seeded):
    a = [d.pk for d in Composer(ovd()).generate(1, seed=3)[0].dishes]
    b = [d.pk for d in Composer(ovd()).generate(1, seed=3)[0].dishes]
    assert a == b


def test_unreachable_norm_is_reported_honestly(seeded):
    c = Composer(Diet.objects.get(code="НБД"))
    assert c.reachable_hint()  # seed cards can't reach НБД — the page says what is missing


def test_validate_rejects_dish_not_allowed(seeded):
    forbidden = Dish.objects.exclude(diets=ovd()).first()
    with pytest.raises(ValueError):
        validate_items(ovd(), [["lunch", forbidden.pk]])
    with pytest.raises(ValueError):
        validate_items(ovd(), [["brunch", ovd().dishes.first().pk]])


def test_fit_page_apply_keeps_history_and_unverifies(seeded, login):
    c = login(Role.DIETICIAN)
    menu = Menu.objects.get(number=1)
    menu.verified = True
    menu.save()
    html = c.get("/menus/1/fit/").content.decode()
    assert "Варіант 1" in html and "у нормі" in html
    option = Composer(ovd()).fit_options(menu)[1][-1]
    items = Composer(ovd()).describe(option)["items"]
    r = c.post("/menus/1/fit/", {"action": "apply", "items": json.dumps(items)})
    assert r.status_code == 302
    menu.refresh_from_db()
    assert not menu.verified
    assert sorted(menu.items.values_list("dish_id", flat=True)) == sorted(pk for _, pk in items)
    assert menu.history.count() >= 2


def test_save_as_new_menu(seeded, login):
    c = login(Role.DIETICIAN)
    option = Composer(ovd()).generate(1, seed=2)[0]
    items = Composer(ovd()).describe(option)["items"]
    before = Menu.objects.count()
    r = c.post("/menus/new/", {"diet": "ОВД", "items": json.dumps(items)})
    new = Menu.objects.order_by("-number").first()
    assert Menu.objects.count() == before + 1 and r.url == f"/menus/{new.number}/"
    assert new.diet.code == "ОВД" and not new.verified and new.items.count() == len(items)


def test_tampered_items_are_rejected(seeded, login):
    c = login(Role.DIETICIAN)
    forbidden = Dish.objects.exclude(diets=ovd()).first()
    before = Menu.objects.count()
    c.post("/menus/new/", {"diet": "ОВД", "items": json.dumps([["lunch", forbidden.pk]])})
    assert Menu.objects.count() == before


def test_generate_page(seeded, login):
    html = login(Role.DIETICIAN).get("/menus/new/?diet=ОВД").content.decode()
    assert html.count("Створити меню") == 3


def test_ai_review_is_advice_only(seeded, login, settings, monkeypatch):
    settings.AI_ENABLED = True
    monkeypatch.setenv(client.KEY_ENV, "sk-ant-test")
    c = login(Role.DIETICIAN)
    items = Composer(ovd()).describe(Composer(ovd()).generate(1, seed=1)[0])["items"]
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return client.Reply({"verdict": "minor", "issues": [{"meal": "Обід", "text": "Дві страви з картоплі — замініть гарнір."}],
                             "summary": "Загалом добре."}, 300, 80)

    before = Menu.objects.count()
    with client.use_transport(fake):
        html = c.post("/menus/review/", {"diet": "ОВД", "items": json.dumps(items)}).content.decode()
    assert "Запропоновано ШІ" in html and "замініть гарнір" in html
    assert Menu.objects.count() == before                       # nothing saved by the model
    assert "Дієта: ОВД" in seen["content"][0]["text"] and AIRequestLog.objects.get().feature == "menu_review"


def test_ai_review_off(seeded, login):
    items = [["lunch", ovd().dishes.first().pk]]
    html = login(Role.DIETICIAN).post("/menus/review/", {"diet": "ОВД", "items": json.dumps(items)}).content.decode()
    assert "ШІ вимкнено" in html


def test_access(seeded, login):
    assert login(Role.CHEF).get("/menus/1/fit/").status_code == 403
    assert login(Role.ACCOUNTANT).get("/menus/new/").status_code == 403
    assert "Підігнати під норму" not in login(Role.CHEF).get("/menus/1/").content.decode()
