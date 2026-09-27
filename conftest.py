from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command

from apps.accounts.models import Role
from apps.accounts.tests.factories import UserFactory


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
