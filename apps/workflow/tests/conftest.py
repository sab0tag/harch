from datetime import date
from decimal import Decimal

import pytest

from apps.accounts.models import Role
from apps.accounts.tests.factories import DepartmentFactory, UserFactory

DAY = date(2026, 10, 1)


@pytest.fixture
def hospital(db):
    """3 departments with one nurse each + one user per other role + a minimal menu world."""
    from apps.catalog.tests.factories import DietFactory, DishFactory, IngredientFactory, MenuFactory, MenuItemFactory

    diet = DietFactory(code="ОВД", has_own_menus=True)
    dish = DishFactory(diets=[diet])
    IngredientFactory(dish=dish, brutto_g=Decimal("100"), netto_g=Decimal("80"))
    MenuItemFactory(menu=MenuFactory(number=1, diet=diet), dish=dish)

    depts = [DepartmentFactory(name=n, default_diet=diet) for n in ("Терапевтичне", "Хірургічне", "Кардіологічне")]
    ns = type("Hospital", (), {})()
    ns.diet = diet
    ns.depts = depts
    ns.nurses = [UserFactory(role=Role.DEPT_NURSE, department=d) for d in depts]
    ns.dietician = UserFactory(role=Role.DIETICIAN)
    ns.dietician2 = UserFactory(role=Role.DIETICIAN)
    ns.chef = UserFactory(role=Role.CHEF)
    ns.accountant = UserFactory(role=Role.ACCOUNTANT)
    ns.admin = UserFactory(role=Role.ADMIN)
    return ns


def fill_doc(task, portions=10):
    """Engine tests: put a valid document behind the task so submit validation passes."""
    from apps.menus.models import Menu
    from apps.orders.models import DailyOrderLine
    from apps.orders.services import open_order
    from apps.planning.services import open_plan, sync_portions
    from apps.workflow.definitions import MENU_PLANNING, ORDER_SUBMISSION, WRITE_OFF

    if task.type == ORDER_SUBMISSION:
        order = open_order(task.department, task.service_date)
        diet = task.department.default_diet
        DailyOrderLine.objects.update_or_create(order=order, diet=diet, defaults={"portions": portions})
    elif task.type == MENU_PLANNING:
        plan = sync_portions(open_plan(task.service_date))
        for entry in plan.entries.all():
            entry.menu = Menu.objects.filter(diet=entry.diet).order_by("number").first()
            entry.save(update_fields=["menu"])
    elif task.type == WRITE_OFF:
        from decimal import Decimal

        from apps.catalog.models import Product

        Product.objects.filter(price_per_kg__isnull=True).update(price_per_kg=Decimal("50.00"))
    task.refresh_from_db()
    return task


def submit(task, user, comment=""):
    from apps.workflow import services as wf

    return wf.submit(fill_doc(task), user, comment)
