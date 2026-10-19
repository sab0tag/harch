"""Regression checks for plan diet consistency (Django test environment)."""
import pytest
from django.core.exceptions import ValidationError
from apps.planning.models import DailyPlanEntry


def test_plan_entry_rejects_mismatched_menu_diet():
    class MenuStub:
        diet_id = 101
        number = 7

    entry = DailyPlanEntry()
    entry.diet_id = 202
    entry.menu_id = 77
    entry._state.fields_cache["menu"] = MenuStub()
    with pytest.raises(ValidationError, match="Меню"):
        entry.clean()


def test_plan_entry_accepts_matching_menu_diet():
    class MenuStub:
        diet_id = 101
        number = 7

    entry = DailyPlanEntry()
    entry.diet_id = 101
    entry.menu_id = 77
    entry._state.fields_cache["menu"] = MenuStub()
    entry.clean()
