from datetime import date

import pytest

from apps.accounts.models import Role
from apps.accounts.tests.factories import UserFactory
from apps.orgstructure.models import Department

DAY = date(2026, 10, 1)
DEPTS = ("Терапевтичне", "Хірургічне", "Ендокринологічне")  # default diets ОВД, ЩД, №9


@pytest.fixture
def world(seeded):
    """Seeded catalog, 3 active departments with a nurse each, plus dietician/chef/accountant/admin."""
    Department.objects.exclude(name__in=DEPTS).update(is_active=False)
    ns = type("World", (), {})()
    ns.depts = {d.name: d for d in Department.objects.filter(name__in=DEPTS)}
    ns.nurse = {name: UserFactory(role=Role.DEPT_NURSE, department=d) for name, d in ns.depts.items()}
    ns.dietician = UserFactory(role=Role.DIETICIAN)
    ns.chef = UserFactory(role=Role.CHEF)
    ns.accountant = UserFactory(role=Role.ACCOUNTANT)
    ns.admin = UserFactory(role=Role.ADMIN)
    return ns


def as_user(client, user):
    client.force_login(user)
    return client
