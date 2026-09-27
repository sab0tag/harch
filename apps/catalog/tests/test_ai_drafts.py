"""AI-07 card draft and AI-06 product estimates: the model drafts, code validates, people accept."""
from decimal import Decimal

import pytest

from apps.accounts.models import Role
from apps.assist import client
from apps.assist.models import AIDraft
from apps.catalog.audit import run_audit
from apps.catalog.models import Diet, Dish, Product, ReferenceFinding

pytestmark = pytest.mark.django_db


@pytest.fixture
def ai_on(settings, monkeypatch):
    settings.AI_ENABLED = True
    monkeypatch.setenv(client.KEY_ENV, "sk-ant-test")


def pid(name):
    return Product.objects.get(name=name).pk


def fake(reply):
    calls = []

    def t(**kw):
        calls.append(kw)
        return client.Reply(reply, 500, 200)

    t.calls = calls
    return t


def dish_reply():
    return {
        "ingredients": [
            {"product_id": pid("Сир кисломолочний 9%"), "brutto_g": 120, "netto_g": 118},
            {"product_id": pid("Борошно пшеничне"), "brutto_g": 15, "netto_g": 15},
            {"product_id": pid("Яйце куряче"), "brutto_g": 12, "netto_g": 10},
            {"product_id": 999999, "brutto_g": 10, "netto_g": 10},              # unknown → dropped
            {"product_id": pid("Цукор"), "brutto_g": 10, "netto_g": 12},        # netto > brutto: kept, flagged on create
        ],
        "output_g": 150, "technology": "Сир протерти, додати борошно і яйце, сформувати, запекти.",
        "missing_products": [{"name": "Сочевиця червона", "reason": "для подачі"}], "warnings": [],
    }


def start(c):
    return c.post("/catalog/dishes/draft/", {"name": "Сирники запечені", "category": "Сніданок — основні",
                                             "output_g": "150", "diets": [Diet.objects.get(code="ОВД").pk]})


def test_dish_draft_nothing_created_until_accepted(seeded, ai_on, login):
    c = login(Role.DIETICIAN)
    t = fake(dish_reply())
    before = Dish.objects.count()
    with client.use_transport(t):
        r = start(c)
    draft = AIDraft.objects.get()
    assert r.url == f"/catalog/dishes/draft/{draft.pk}/" and Dish.objects.count() == before
    assert [i["product_id"] for i in draft.result["ingredients"]] == [pid("Сир кисломолочний 9%"), pid("Борошно пшеничне"), pid("Яйце куряче"), pid("Цукор")]
    assert "ШІ вказав продукти, яких немає" in draft.result["warnings"][-1]
    assert "Доступні продукти" in t.calls[0]["content"][0]["text"]
    html = c.get(r.url).content.decode()
    assert "Запропоновано ШІ" in html and "Сочевиця червона" in html and "Запропонувати ці продукти" in html


def draft_form(draft, **over):
    rows = draft.result["ingredients"]
    data = {"rows": str(len(rows)), "card_no": "900", "name": "Сирники запечені", "category": "Сніданок — основні",
            "output_g": "150", "diets": [Diet.objects.get(code="ОВД").pk], "technology": "…", "action": "create"}
    for i, r in enumerate(rows):
        data.update({f"product_{i}": r["product_id"], f"brutto_{i}": r["brutto_g"], f"netto_{i}": r["netto_g"]})
    data.update(over)
    return data


def test_create_validates_in_code_then_creates_unverified(seeded, ai_on, login):
    c = login(Role.DIETICIAN)
    with client.use_transport(fake(dish_reply())):
        start(c)
    draft = AIDraft.objects.get()
    html = c.post(f"/catalog/dishes/draft/{draft.pk}/", draft_form(draft)).content.decode()
    assert "нетто не може бути більше за брутто" in html and not Dish.objects.filter(card_no=900).exists()
    r = c.post(f"/catalog/dishes/draft/{draft.pk}/", draft_form(draft, netto_3="9"))
    dish = Dish.objects.get(card_no=900)
    assert r.url == f"/catalog/dishes/{dish.pk}/"
    assert (dish.verified, dish.origin, dish.ingredients.count()) == (False, "ai_draft", 4)
    assert list(dish.diets.values_list("code", flat=True)) == ["ОВД"]
    draft.refresh_from_db()
    assert draft.status == "accepted" and draft.created_ids == [dish.pk]
    # accepted draft can't create a second card
    c.post(f"/catalog/dishes/draft/{draft.pk}/", draft_form(draft, card_no="901", netto_3="9"))
    assert not Dish.objects.filter(card_no=901).exists()


def test_existing_card_no_rejected(seeded, ai_on, login):
    c = login(Role.DIETICIAN)
    with client.use_transport(fake(dish_reply())):
        start(c)
    draft = AIDraft.objects.get()
    html = c.post(f"/catalog/dishes/draft/{draft.pk}/", draft_form(draft, card_no="1", netto_3="9")).content.decode()
    assert "Картка №1 вже існує" in html


def test_other_users_draft_is_hidden(seeded, ai_on, login, make_user):
    c = login(Role.DIETICIAN)
    with client.use_transport(fake(dish_reply())):
        start(c)
    draft = AIDraft.objects.get()
    other = make_user(role=Role.DIETICIAN)
    c.force_login(other)
    assert c.get(f"/catalog/dishes/draft/{draft.pk}/").status_code == 404


def products_reply():
    return {"products": [
        {"input_name": "Сочевиця червона", "name": "Сочевиця червона", "category": None, "protein": 2.6, "fat": 15,
         "carbs": 3.6, "kcal": 160, "waste_pct": 0, "confidence": "high", "note": ""},
        {"input_name": "шпинат", "name": "Шпинат", "category": None, "protein": 2.8, "fat": 0.4,
         "carbs": 6.6, "kcal": 90, "waste_pct": 30, "confidence": "medium", "note": "свіжа"},      # kcal mismatch
        {"input_name": "молоко", "name": "Молоко 2.5%", "category": None, "protein": 2.8, "fat": 2.5,
         "carbs": 4.7, "kcal": 52, "waste_pct": 0, "confidence": "high", "note": ""},             # already exists
        {"input_name": "x", "name": "Погане", "protein": -1, "fat": 0, "carbs": 0, "kcal": 0,
         "confidence": "low", "note": ""},                                                        # invalid → dropped
    ]}


def test_product_estimates_flow_back_to_dish_draft(seeded, ai_on, login):
    c = login(Role.DIETICIAN)
    with client.use_transport(fake(dish_reply())):
        start(c)
    dish_draft = AIDraft.objects.get()
    with client.use_transport(fake(products_reply())):
        r = c.post("/catalog/products/suggest/", {"names": "Сочевиця червона\nшпинат\nмолоко\nx", "back": dish_draft.pk})
    pdraft = AIDraft.objects.get(kind="products")
    rows = pdraft.result["products"]
    assert [x["name"] for x in rows] == ["Сочевиця червона", "Шпинат", "Молоко 2.5%"]
    assert rows[1]["kcal_mismatch"] and rows[2]["exists"] == "Молоко 2.5%"
    html = c.get(r.url).content.decode()
    assert "вже є" in html and "Оцінка ШІ" in html
    data = {"action": "create"}
    for i, x in enumerate(rows[:2]):
        data.update({f"take_{i}": "on", f"name_{i}": x["name"], f"category_{i}": "",
                     **{f"{f}_{i}": x[f] for f in ("protein", "fat", "carbs", "kcal")}})
    r = c.post(f"/catalog/products/suggest/{pdraft.pk}/", data)
    assert r.status_code == 302, [m for m in r.content.decode().split("alert-danger")[1:]]
    assert r.url == f"/catalog/dishes/draft/{dish_draft.pk}/"      # back to the card draft
    sour = Product.objects.get(name="Сочевиця червона")
    assert (sour.origin, sour.verified, sour.fat) == ("ai_estimate", False, Decimal("15.00"))
    assert "звірити з довідником" in sour.source
    run_audit()
    assert ReferenceFinding.objects.filter(kind="product_ai_estimate", obj_id=sour.pk, status="open").exists()
    sour.verified = True
    sour.save()
    run_audit()
    assert ReferenceFinding.objects.get(kind="product_ai_estimate", obj_id=sour.pk).status == "resolved"


def test_duplicate_name_cannot_be_added(seeded, ai_on, login):
    c = login(Role.DIETICIAN)
    with client.use_transport(fake(products_reply())):
        c.post("/catalog/products/suggest/", {"names": "молоко"})
    pdraft = AIDraft.objects.get()
    before = Product.objects.count()
    html = c.post(f"/catalog/products/suggest/{pdraft.pk}/", {"action": "create", "take_2": "on", "name_2": "молоко 2.5 %",
                  "protein_2": "2.8", "fat_2": "2.5", "carbs_2": "4.7", "kcal_2": "52"}).content.decode()
    assert "вже є в довіднику" in html and Product.objects.count() == before


def test_ai_off_shows_message_and_buttons_hidden(seeded, login):
    c = login(Role.DIETICIAN)
    assert "Чернетка з ШІ" not in c.get("/catalog/dishes/").content.decode()
    html = start(c).content.decode()
    assert "ШІ вимкнено" in html and not AIDraft.objects.exists()


def test_access(seeded, ai_on, login):
    assert login(Role.CHEF).get("/catalog/dishes/draft/").status_code == 403
    assert login(Role.ACCOUNTANT).get("/catalog/products/suggest/").status_code == 403
