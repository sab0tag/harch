from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import time_machine
from django.conf import settings
from django.core.management import call_command

from apps.accounts.models import Role
from apps.accounts.tests.factories import UserFactory


# Test data is written for service dates around 1 October 2026 (DAY in the app conftests). Past service
# dates behave differently (a nurse may no longer submit), so "today" is pinned here; the clock still ticks.
TODAY = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Europe/Kyiv"))


@pytest.fixture(autouse=True)
def _pinned_today():
    with time_machine.travel(TODAY, tick=True):
        yield


@pytest.fixture
def make_user(db):
    def _make(role=Role.DIETICIAN, **kwargs):
        return UserFactory(role=role, **kwargs)

    return _make


@pytest.fixture
def login(client, make_user):
    """login(role) -> logged-in client for a fresh user with that role."""

    def _login(role, **kwargs):
        user = make_user(role=role, **kwargs)
        client.force_login(user)
        client.user = user
        return client

    return _login


@pytest.fixture
def seeded(db):
    """Full starter data from seed/ (8 diets, 72 products, 130 cards, 250 menus)."""
    call_command("load_seed", str(Path(settings.BASE_DIR) / "seed"), verbosity=0, stdout=open("/dev/null", "w"))


@pytest.fixture(autouse=True)
def _no_real_ai(monkeypatch):
    """Tests never reach the Anthropic API (CLAUDE.md). Tests that need a reply install a fake."""
    from apps.assist import client

    def _refuse(**kwargs):
        raise RuntimeError("Real Anthropic API must not be called from tests")

    monkeypatch.setattr(client, "_transport", _refuse)
    monkeypatch.delenv(client.KEY_ENV, raising=False)
