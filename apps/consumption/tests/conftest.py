import pytest

from apps.catalog.models import Diet
from apps.orders.services import open_order, submit_order
from apps.orders.tests.conftest import DAY, as_user, world  # noqa: F401  (fixtures)
from apps.planning.services import open_plan
from apps.workflow import services as wf
from apps.workflow.definitions import COOKING, MENU_PLANNING, WRITE_OFF
from apps.workflow.models import Task, TaskStatus

ORDERS = {"Терапевтичне": {"ОВД": 30}, "Хірургічне": {"ЩД": 20}, "Ендокринологічне": {"№9": 8, "ОВД": 2}}


def diet(code):
    return Diet.objects.get(code=code)


@pytest.fixture
def cooking_day(world):
    """Orders approved → plan (menus by rotation) submitted by dietician and approved by chef."""
    for name, portions in ORDERS.items():
        submit_order(open_order(world.depts[name], DAY), world.nurse[name], {diet(c).pk: v for c, v in portions.items()})
    for task in Task.objects.filter(status=TaskStatus.SUBMITTED):
        wf.approve(task, world.dietician)
    plan = open_plan(DAY)
    wf.submit(Task.objects.get(type=MENU_PLANNING, service_date=DAY), world.dietician)
    wf.approve(Task.objects.get(type=MENU_PLANNING, service_date=DAY), world.chef)
    plan.refresh_from_db()
    world.plan = plan
    world.cooking = Task.objects.get(type=COOKING, service_date=DAY)
    return world


@pytest.fixture
def write_off_day(cooking_day):
    w = cooking_day
    from apps.consumption.services import ensure_actuals

    ensure_actuals(w.plan)
    from apps.core.testing import on_the_day

    with on_the_day():
        wf.submit(w.cooking, w.chef)
    wf.approve(w.cooking, w.dietician)
    w.write_off = Task.objects.get(type=WRITE_OFF, service_date=DAY)
    return w
