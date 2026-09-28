"""S5-07: import product prices from the accountant's Excel."""
from decimal import Decimal
from io import BytesIO

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook

from apps.accounts.models import Role
from apps.catalog import price_import as pi
from apps.catalog.models import PriceImport, PriceImportRow, Product, ProductAlias
from apps.catalog.tests.factories import ProductFactory

pytestmark = pytest.mark.django_db
START = "/catalog/products/prices/import/"


def xlsx(rows, name="prices.xlsx", title="Прайс"):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(name, buf.getvalue(),
                              content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# A realistic accountant's sheet: a title above the header, a unit column, a section line, a total.
SHEET = [
    ["КНП «Лікарня» — ціни на продукти, вересень 2026"],
    [],
    ["№", "Найменування", "Од. вим.", "Ціна, грн"],
    [None, "Бакалія"],
    [1, "Крупа гречана", "кг", 52.4],
    [2, "Олія соняшникова", "л", "78,90"],
    [3, "Філе куряче", "кг", "189,00"],
    [4, "Сіль йодована", "г", "0,02"],
    [5, "Яйце куряче", "шт", "5,50"],
    [6, "Кава зернова", "кг", "640"],
    [7, "Молоко 2,5%", "кг", "ціна уточнюється"],
    [None, "Разом", None, "1500"],
]


@pytest.fixture
def products():
    return {
        "гречка": ProductFactory(name="Крупа гречана", price_per_kg=Decimal("48.00")),
        "олія": ProductFactory(name="Олія соняшникова", price_per_kg=None),
        "курка": ProductFactory(name="Курка, філе", price_per_kg=Decimal("150.00")),
        "сіль": ProductFactory(name="Сіль йодована", price_per_kg=Decimal("15.00")),
        "яйце": ProductFactory(name="Яйце куряче", price_per_kg=Decimal("5.00")),
        "молоко": ProductFactory(name="Молоко 2,5%", price_per_kg=Decimal("40.00")),
    }


def upload(login, rows=SHEET, role=Role.ACCOUNTANT, **kw):
    client = login(role)
    response = client.post(START, {"file": xlsx(rows, **kw)})
    assert response.status_code == 302, response.content[:300]
    return client, PriceImport.objects.latest("pk")


def by_name(imp):
    return {r.raw_name: r for r in imp.rows.select_related("product")}


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize("text,value", [
    ("45,50", "45.50"), ("45.5", "45.5"), ("1 234,50", "1234.50"), ("1\xa0234.50 грн", "1234.50"),
    ("1.234,50", "1234.50"), ("1,234.50", "1234.50"), ("78.9 ₴", "78.9"), ("640", "640"),
])
def test_parse_price(text, value):
    assert pi.parse_price(text) == Decimal(value)


@pytest.mark.parametrize("text", ["", "ціна уточнюється", "0", "-5", "45,5,5"])
def test_parse_price_rejects(text):
    assert pi.parse_price(text) is None


@pytest.mark.parametrize("raw,expected", [
    ("Крупа гречана", "Гречка"),                  # common word «крупа» must not win over «гречка»
    ("Буряк столовий", "Буряк"),                  # extra words in the file are fine
    ("Цукор-пісок", "Цукор"),
    ("Філе куряче", "Курка, філе"),               # word order
    ("Яйця курячі", "Яйце куряче"),               # word forms
    ("Масло вершкове 72,5%", "Масло вершкове"),   # a number on one side only
    ("Кефір 3,2%", None),                         # different numbers never match
    ("Печиво", None),                             # ambiguous: two kinds of печиво
])
def test_matcher_on_accountant_names(raw, expected):
    names = ["Гречка", "Крупа ячна", "Крупа манна", "Перлова крупа", "Буряк", "Цукор", "Цукрозамінник", "Курка, філе",
             "Курятина", "Яйце куряче", "Масло вершкове", "Кефір 1%", "Кефір 2.5%", "Печиво галетне", "Печиво дієтичне"]
    matcher = pi.Matcher([ProductFactory(name=n) for n in names])
    product, match, _ = matcher.match(raw)
    assert (product.name if product else None) == expected
    assert match == ("fuzzy" if expected else "none")


# ------------------------------------------------------------------ happy path
def test_detects_columns_and_matches_rows(login, products):
    _, imp = upload(login)
    assert (imp.header_row, imp.name_col, imp.unit_col, imp.price_col) == (2, 1, 2, 3)
    rows = by_name(imp)
    assert "Бакалія" not in rows and "Разом" not in rows          # section title and total skipped

    buckwheat = rows["Крупа гречана"]
    assert (buckwheat.product, buckwheat.match, buckwheat.price_per_kg) == (products["гречка"], "exact", Decimal("52.40"))
    assert buckwheat.old_price == Decimal("48.00") and buckwheat.selected

    assert rows["Олія соняшникова"].price_per_kg == Decimal("78.90")     # л counts as kg
    assert rows["Сіль йодована"].price_per_kg == Decimal("20.00")        # 0,02 грн/г → 20 грн/кг

    chicken = rows["Філе куряче"]                                       # different word order
    assert (chicken.product, chicken.match) == (products["курка"], "fuzzy")
    assert not chicken.selected                                        # waits for a person

    assert "шт" in rows["Яйце куряче"].problem and not rows["Яйце куряче"].selected
    assert rows["Молоко 2,5%"].problem and rows["Молоко 2,5%"].price_per_kg is None
    assert rows["Кава зернова"].match == "none" and rows["Кава зернова"].product is None


def test_apply_changes_only_ticked_rows_with_history(login, products):
    client, imp = upload(login)
    rows = by_name(imp)
    chicken = rows["Філе куряче"]
    data = {"action": "apply", f"apply_{rows['Крупа гречана'].pk}": "1", f"product_{rows['Крупа гречана'].pk}": products["гречка"].pk,
            f"apply_{chicken.pk}": "1", f"product_{chicken.pk}": products["курка"].pk,
            f"product_{rows['Олія соняшникова'].pk}": products["олія"].pk}       # olive oil left unticked
    client.post(f"{START}{imp.pk}/", data)

    imp.refresh_from_db()
    assert imp.status == "applied" and imp.applied_count == 2
    prices = {k: Product.objects.get(pk=p.pk).price_per_kg for k, p in products.items()}
    assert prices["гречка"] == Decimal("52.40") and prices["курка"] == Decimal("189.00")
    assert prices["олія"] is None and prices["сіль"] == Decimal("15.00")
    last = products["гречка"].history.first()
    assert last.history_change_reason == "Імпорт цін: prices.xlsx"
    assert last.history_user == client.user
    assert prices["яйце"] == Decimal("5.00")                        # «шт» row cannot be applied


def test_confirmed_names_are_remembered_for_next_file(login, products):
    client, imp = upload(login)
    rows = by_name(imp)
    chicken, coffee = rows["Філе куряче"], rows["Кава зернова"]
    other = ProductFactory(name="Кава мелена")
    client.post(f"{START}{imp.pk}/", {"action": "apply", f"apply_{chicken.pk}": "1", f"product_{chicken.pk}": products["курка"].pk,
                                      f"apply_{coffee.pk}": "1", f"product_{coffee.pk}": other.pk})
    assert set(ProductAlias.objects.values_list("name", flat=True)) == {"філе куряче", "кава зернова"}

    _, second = upload(login, name="prices-october.xlsx")
    rows = by_name(second)
    assert (rows["Філе куряче"].match, rows["Філе куряче"].product) == ("alias", products["курка"])
    assert (rows["Кава зернова"].match, rows["Кава зернова"].product) == ("alias", other)
    # Same headers as last time → the same columns are used without asking.
    assert (second.header_row, second.name_col, second.price_col) == (2, 1, 3)


def test_manual_column_choice_when_headers_unknown(login, products):
    client, imp = upload(login, rows=[["Товар", "Сума"], ["Крупа гречана", "55"]])
    assert not imp.is_mapped and imp.rows.count() == 0
    client.post(f"{START}{imp.pk}/", {"action": "map", "sheet": imp.sheet, "header_row": "0",
                                      "name_col": "0", "price_col": "1", "unit": "100g"})
    imp.refresh_from_db()
    row = imp.rows.get()
    assert row.product == products["гречка"] and row.price_per_kg == Decimal("550.00")   # per 100 g → per kg


def test_same_product_twice_with_different_prices_is_refused(login, products):
    client, imp = upload(login, rows=[["Назва", "Ціна"], ["Крупа гречана", "50"], ["Гречана крупа", "60"]])
    a, b = imp.rows.all()
    r = client.post(f"{START}{imp.pk}/", {"action": "apply", f"apply_{a.pk}": "1", f"product_{a.pk}": products["гречка"].pk,
                                          f"apply_{b.pk}": "1", f"product_{b.pk}": products["гречка"].pk}, follow=True)
    assert "залиште один" in r.content.decode()
    imp.refresh_from_db()
    assert imp.status == "draft"
    assert Product.objects.get(pk=products["гречка"].pk).price_per_kg == Decimal("48.00")


def test_ticked_row_without_product_is_refused(login, products):
    client, imp = upload(login)
    coffee = by_name(imp)["Кава зернова"]
    r = client.post(f"{START}{imp.pk}/", {"action": "apply", f"apply_{coffee.pk}": "1"}, follow=True)
    assert "оберіть продукт" in r.content.decode()


def test_applied_or_cancelled_import_is_closed(login, products):
    client, imp = upload(login)
    client.post(f"{START}{imp.pk}/", {"action": "cancel"})
    imp.refresh_from_db()
    assert imp.status == "cancelled"
    with pytest.raises(ValidationError):
        pi.apply(imp, client.user, {})
    with pytest.raises(ValidationError):
        pi.set_mapping(imp, imp.sheet, 0, 0, 1)


def test_bad_files_are_refused_with_a_message(login):
    client = login(Role.ACCOUNTANT)
    r = client.post(START, {"file": SimpleUploadedFile("old.xls", b"\xd0\xcf\x11\xe0")}, follow=True)
    assert "збережіть як .xlsx" in r.content.decode()
    r = client.post(START, {"file": SimpleUploadedFile("x.xlsx", b"not a zip")}, follow=True)
    assert "Не вдалося прочитати файл" in r.content.decode()
    assert PriceImport.objects.count() == 0


def test_review_page_renders(login, products):
    client, imp = upload(login)
    html = client.get(f"{START}{imp.pk}/").content.decode()
    assert "Філе куряче" in html and "Схожа назва" in html and "Застосувати позначені" in html
    assert "Імпорт цін з Excel" in client.get("/catalog/products/").content.decode()


# ------------------------------------------------------------------ access
@pytest.mark.parametrize("role,status", [
    (Role.ACCOUNTANT, 200), (Role.DIETICIAN, 200),
    (Role.ADMIN, 200), (Role.CHEF, 403), (Role.DEPT_NURSE, 403),
])
def test_access(login, role, status):
    assert login(role).get(START).status_code == status


def test_other_roles_cannot_apply(login, make_user, client, products):
    _, imp = upload(login)
    client.force_login(make_user(role=Role.CHEF))
    row = by_name(imp)["Крупа гречана"]
    assert client.post(f"{START}{imp.pk}/", {"action": "apply", f"apply_{row.pk}": "1"}).status_code == 403
    assert Product.objects.get(pk=products["гречка"].pk).price_per_kg == Decimal("48.00")
