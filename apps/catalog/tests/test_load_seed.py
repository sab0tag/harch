import json
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command

from apps.catalog.models import Diet, Dish, DishIngredient, Origin, Product
from apps.menus.models import Menu, MenuItem
from apps.orgstructure.models import Department

pytestmark = pytest.mark.django_db
SEED = Path(settings.BASE_DIR) / "seed"


def load():
    out = StringIO()
    call_command("load_seed", str(SEED), stdout=out)
    return out.getvalue()


def test_counts_match_spec():
    load()
    assert Diet.objects.count() == 8
    assert Product.objects.count() == 72
    assert Department.objects.count() == 15
    assert Dish.objects.count() == 130
    assert Menu.objects.count() == 250


def test_menu_items_match_seed_file():
    load()
    menus = json.loads((SEED / "menus.json").read_text(encoding="utf-8"))
    expected = sum(len(v) for m in menus for v in m["meals"].values())
    assert MenuItem.objects.count() == expected


def test_every_menu_item_is_allowed_for_its_diet():
    output = load()
    assert "WARNING" not in output
    from django.db.models import Exists, OuterRef

    allowed = Dish.diets.through.objects.filter(dish=OuterRef("dish"), diet=OuterRef("menu__diet"))
    assert not MenuItem.objects.exclude(Exists(allowed)).exists()


def test_everything_is_unverified_and_marked_as_seed():
    load()
    assert not Dish.objects.filter(verified=True).exists()
    assert not Menu.objects.filter(verified=True).exists()
    assert not Product.objects.filter(verified=True).exists()
    assert set(Dish.objects.values_list("origin", flat=True)) == {Origin.SEED}


def test_rerun_creates_nothing():
    load()
    before = (Dish.objects.count(), DishIngredient.objects.count(), MenuItem.objects.count())
    output = load()
    assert (Dish.objects.count(), DishIngredient.objects.count(), MenuItem.objects.count()) == before
    assert "created    0" in output


def test_rerun_never_overwrites_edits():
    load()
    Product.objects.filter(name="Цукор").update(kcal=Decimal("123"))
    Dish.objects.filter(card_no=1).update(name="Відредаговано дієтсестрою")
    load()
    assert Product.objects.get(name="Цукор").kcal == Decimal("123")
    assert Dish.objects.get(card_no=1).name == "Відредаговано дієтсестрою"


def test_fills_missing_default_diet_on_existing_department():
    Department.objects.create(name="Терапевтичне")
    load()
    assert Department.objects.get(name="Терапевтичне").default_diet.code == "ОВД"


def test_department_patient_categories_mapped():
    load()
    assert Department.objects.get(name="Пологове").patient_category == Department.PatientCategory.MATERNITY


def test_special_diets_without_menus():
    load()
    assert not Diet.objects.get(code="№4").has_own_menus
    assert Diet.objects.get(code="№9").has_own_menus


def test_missing_folder_is_an_error(tmp_path):
    with pytest.raises(CommandError):
        call_command("load_seed", str(tmp_path / "nope"))
