"""S1-03: catalog screens — access, filters, HTMX ingredient editing, product price rules."""
from decimal import Decimal

import pytest

from apps.accounts.models import Role
from apps.catalog.models import Dish, DishIngredient, Product
from apps.catalog.tests.factories import DietFactory, DishFactory, IngredientFactory, ProductFactory

pytestmark = pytest.mark.django_db
R = Role


@pytest.mark.parametrize(
    "url,allowed",
    [
        ("/catalog/dishes/", {R.ADMIN, R.DIETICIAN, R.CHEF, R.ACCOUNTANT}),
        ("/catalog/products/", {R.ADMIN, R.DIETICIAN, R.CHEF, R.ACCOUNTANT, R.STOREKEEPER}),
        ("/catalog/diets/", {R.ADMIN, R.DIETICIAN, R.CHEF}),
        ("/org/departments/", {R.ADMIN, R.DIETICIAN}),
        ("/catalog/dishes/new/", {R.ADMIN, R.DIETICIAN}),
        ("/catalog/products/new/", {R.ADMIN, R.DIETICIAN}),
    ],
)
@pytest.mark.parametrize("role", list(R))
def test_screen_access_matrix(login, url, allowed, role):
    status = login(role).get(url).status_code
    assert status == (200 if role in allowed else 403), f"{role} {url}"


def test_dish_detail_readonly_for_chef(login):
    dish = DishFactory()
    IngredientFactory(dish=dish)
    html = login(R.CHEF).get(f"/catalog/dishes/{dish.pk}/").content.decode()
    assert "Склад на 1 порцію" in html
    assert "ingredient-add" not in html and "Редагувати опис" not in html


def test_dish_list_filters(login):
    ovd, nkd = DietFactory(code="ОВД"), DietFactory(code="НКД")
    DishFactory(name="Каша гречана", diets=[ovd], verified=True)
    DishFactory(name="Суп овочевий", diets=[nkd])
    client = login(R.DIETICIAN)
    assert "Суп овочевий" not in client.get("/catalog/dishes/?q=каша").content.decode()
    html = client.get("/catalog/dishes/?diet=НКД").content.decode()
    assert "Суп овочевий" in html and "Каша гречана" not in html
    html = client.get("/catalog/dishes/?state=unverified").content.decode()
    assert "Суп овочевий" in html and "Каша гречана" not in html


def test_dish_list_search_by_card_number(login):
    DishFactory(card_no=77, name="Сирники")
    assert "Сирники" in login(R.DIETICIAN).get("/catalog/dishes/?q=77").content.decode()


def test_create_dish(login):
    diet = DietFactory(code="ОВД")
    response = login(R.DIETICIAN).post("/catalog/dishes/new/", {
        "card_no": 500, "name": "Нова страва", "category": "Обід — супи", "output_g": "250",
        "diets": [diet.pk], "is_active": "on",
    })
    dish = Dish.objects.get(card_no=500)
    assert response.status_code == 302 and response.url == f"/catalog/dishes/{dish.pk}/"
    assert list(dish.diets.all()) == [diet]


class TestIngredientEditing:
    def url(self, dish, *parts):
        return "/".join([f"/catalog/dishes/{dish.pk}/ingredients", *map(str, parts)]) + "/"

    def test_add_valid_returns_whole_card_with_totals(self, login):
        dish = DishFactory()
        product = ProductFactory(kcal=Decimal("400"))
        r = login(R.DIETICIAN).post(self.url(dish, "add"), {"new-product": product.pk, "new-brutto_g": "60", "new-netto_g": "50"})
        assert r.status_code == 200
        html = r.content.decode()
        assert 'id="ingredients-card"' in html and 'id="dish-totals"' in html
        assert "200" in html  # 50 g × 400 kcal / 100
        assert dish.ingredients.count() == 1

    def test_add_netto_above_brutto_is_rejected(self, login):
        dish = DishFactory()
        r = login(R.DIETICIAN).post(self.url(dish, "add"), {"new-product": ProductFactory().pk, "new-brutto_g": "5", "new-netto_g": "8"})
        assert "Нетто не може бути більше за брутто" in r.content.decode()
        assert not dish.ingredients.exists()

    def test_add_duplicate_product_is_rejected(self, login):
        ing = IngredientFactory()
        r = login(R.DIETICIAN).post(self.url(ing.dish, "add"), {"new-product": ing.product.pk, "new-brutto_g": "5", "new-netto_g": "5"})
        assert "Цей продукт уже є в картці" in r.content.decode()
        assert ing.dish.ingredients.count() == 1

    def test_update(self, login):
        ing = IngredientFactory(brutto_g=Decimal("100"), netto_g=Decimal("80"))
        login(R.DIETICIAN).post(self.url(ing.dish, ing.pk), {
            f"i{ing.pk}-product": ing.product.pk, f"i{ing.pk}-brutto_g": "120", f"i{ing.pk}-netto_g": "90"})
        ing.refresh_from_db()
        assert (ing.brutto_g, ing.netto_g) == (Decimal("120"), Decimal("90"))

    def test_invalid_update_is_not_saved(self, login):
        ing = IngredientFactory(brutto_g=Decimal("100"), netto_g=Decimal("80"))
        r = login(R.DIETICIAN).post(self.url(ing.dish, ing.pk), {
            f"i{ing.pk}-product": ing.product.pk, f"i{ing.pk}-brutto_g": "100", f"i{ing.pk}-netto_g": "150"})
        assert "Нетто не може" in r.content.decode()
        ing.refresh_from_db()
        assert ing.netto_g == Decimal("80")

    def test_delete(self, login):
        ing = IngredientFactory()
        login(R.DIETICIAN).post(self.url(ing.dish, ing.pk, "delete"))
        assert not DishIngredient.objects.filter(pk=ing.pk).exists()

    def test_cannot_touch_ingredient_of_another_dish(self, login):
        ing = IngredientFactory()
        other = DishFactory()
        assert login(R.DIETICIAN).post(self.url(other, ing.pk, "delete")).status_code == 404
        assert DishIngredient.objects.filter(pk=ing.pk).exists()

    @pytest.mark.parametrize("role", [R.CHEF, R.ACCOUNTANT, R.DEPT_NURSE])
    def test_non_editors_are_forbidden(self, login, role):
        ing = IngredientFactory()
        client = login(role)
        assert client.post(self.url(ing.dish, "add"), {}).status_code == 403
        assert client.post(self.url(ing.dish, ing.pk), {}).status_code == 403
        assert client.post(self.url(ing.dish, ing.pk, "delete")).status_code == 403
        assert DishIngredient.objects.filter(pk=ing.pk).exists()

    def test_edits_are_recorded_in_history(self, login):
        ing = IngredientFactory()
        client = login(R.DIETICIAN)
        client.post(self.url(ing.dish, ing.pk, "delete"))
        html = " ".join(client.get(f"/catalog/dishes/{ing.dish.pk}/").content.decode().split())
        assert f"видалено інгредієнт «{ing.product.name}»" in html
        assert client.user.last_name in html


def test_verify_toggle(login):
    dish = DishFactory()
    client = login(R.DIETICIAN)
    client.post(f"/catalog/dishes/{dish.pk}/verify/", {"verified": "1"})
    dish.refresh_from_db()
    assert dish.verified
    client.post(f"/catalog/dishes/{dish.pk}/verify/", {"verified": "0"})
    dish.refresh_from_db()
    assert not dish.verified


def test_verify_forbidden_for_chef(login):
    dish = DishFactory()
    assert login(R.CHEF).post(f"/catalog/dishes/{dish.pk}/verify/").status_code == 403


class TestProducts:
    def test_accountant_changes_price_only(self, login):
        p = ProductFactory(kcal=Decimal("300"))
        client = login(R.ACCOUNTANT)
        assert "Ціна" in client.get(f"/catalog/products/{p.pk}/edit/").content.decode()
        client.post(f"/catalog/products/{p.pk}/edit/", {"price_per_kg": "45.50", "kcal": "999", "name": "Хак"})
        p.refresh_from_db()
        assert p.price_per_kg == Decimal("45.50")
        assert p.kcal == Decimal("300") and p.name != "Хак"

    def test_dietician_full_edit(self, login):
        p = ProductFactory()
        login(R.DIETICIAN).post(f"/catalog/products/{p.pk}/edit/", {
            "name": p.name, "unit": "г", "protein": "1", "fat": "2", "carbs": "3", "kcal": "30",
            "price_per_kg": "", "is_active": "on", "verified": "on"})
        p.refresh_from_db()
        assert p.kcal == Decimal("30") and p.verified

    def test_chef_cannot_edit(self, login):
        assert login(R.CHEF).get(f"/catalog/products/{ProductFactory().pk}/edit/").status_code == 403

    def test_negative_price_rejected(self, login):
        p = ProductFactory()
        login(R.ACCOUNTANT).post(f"/catalog/products/{p.pk}/edit/", {"price_per_kg": "-1"})
        p.refresh_from_db()
        assert p.price_per_kg is None

    def test_dietician_creates_product(self, login):
        login(R.DIETICIAN).post("/catalog/products/new/", {
            "name": "Мед", "unit": "г", "protein": "0.8", "fat": "0", "carbs": "80.3", "kcal": "328", "is_active": "on"})
        assert Product.objects.filter(name="Мед").exists()


def test_seeded_screens_render(seeded, login):
    client = login(R.DIETICIAN)
    html = client.get("/catalog/dishes/").content.decode()
    assert "Не перевірено карток: <strong>130</strong>" in html
    card1 = Dish.objects.get(card_no=1)
    assert "Каша вівсяна на молоці" in client.get(f"/catalog/dishes/{card1.pk}/").content.decode()
    assert "ОВД" in client.get("/catalog/diets/").content.decode()
    assert "Пологове" in client.get("/org/departments/").content.decode()
