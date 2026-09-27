from datetime import date

import pytest

from apps.accounts.models import Role
from apps.accounts.tests.factories import DepartmentFactory, UserFactory

DAY = date(2026, 10, 1)


@pytest.fixture
def hospital(db):
    """3 departments with one nurse each + one user per other role."""
    depts = [DepartmentFactory(name=n) for n in ("Терапевтичне", "Хірургічне", "Кардіологічне")]
    ns = type("Hospital", (), {})()
    ns.depts = depts
    ns.nurses = [UserFactory(role=Role.DEPT_NURSE, department=d) for d in depts]
    ns.dietician = UserFactory(role=Role.DIETICIAN)
    ns.dietician2 = UserFactory(role=Role.DIETICIAN)
    ns.chef = UserFactory(role=Role.CHEF)
    ns.accountant = UserFactory(role=Role.ACCOUNTANT)
    ns.admin = UserFactory(role=Role.ADMIN)
    return ns
