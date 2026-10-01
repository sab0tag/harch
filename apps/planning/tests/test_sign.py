"""The head doctor signs the approved menu-rozkladka in the system."""
import pytest
from django.core.exceptions import PermissionDenied
from django.template.loader import render_to_string

from apps.accounts.models import Role
from apps.accounts.tests.factories import UserFactory
from apps.consumption.tests.conftest import DAY, cooking_day, world  # noqa: F401
from apps.planning.services import sign_plan
from apps.reports.pdf import menu_rozkladka_context
from apps.workflow.models import Notification

pytestmark = pytest.mark.django_db


@pytest.fixture
def director(db):
    return UserFactory(role=Role.DIRECTOR)


def test_director_is_told_signs_and_the_form_says_so(client, director, cooking_day):
    plan = cooking_day.plan
    client.force_login(director)
    html = client.get("/plan/sign/").content.decode()
    assert "Меню-розкладка на 01.10.2026" in html and "Підписати" in html
    form = render_to_string("reports/menu_rozkladka.html", menu_rozkladka_context(plan))
    assert "ЗАТВЕРДЖУЮ" in form and "підписано в системі" not in form
    client.post("/plan/sign/", {"plan": plan.pk})
    plan.refresh_from_db()
    assert plan.signed_at and plan.signed_by == director
    form = render_to_string("reports/menu_rozkladka.html", menu_rozkladka_context(plan))
    assert "ЗАТВЕРДЖЕНО" in form and "підписано в системі «Харчоблок»" in form
    assert director.last_name not in form                      # no name on the form, only the title
    assert "Підписано" in client.get(f"/plan/{DAY.isoformat()}/").content.decode()


def test_director_gets_the_notification_when_the_plan_is_approved(director, world):
    from apps.planning.models import DailyPlan
    from apps.planning.services import notify_directors

    plan = DailyPlan.objects.create(service_date=DAY)
    notify_directors(plan)
    assert Notification.objects.filter(user=director, title="На підпис: меню-розкладка на 01.10").exists()


@pytest.mark.parametrize("role", [Role.ADMIN, Role.CHEF, Role.DIETICIAN])
def test_only_the_director_signs(role, cooking_day):
    with pytest.raises(PermissionDenied):
        sign_plan(cooking_day.plan, UserFactory(role=role))


def test_an_unapproved_plan_cannot_be_signed(director, world):
    from apps.planning.models import DailyPlan

    with pytest.raises(PermissionDenied):
        sign_plan(DailyPlan.objects.create(service_date=DAY), director)


def test_sign_page_is_for_director_and_admin_only(client, director, world):
    client.force_login(world.chef)
    assert client.get("/plan/sign/").status_code == 403
    client.force_login(director)
    assert client.get("/plan/sign/").status_code == 200
    assert client.get("/orders/summary/").status_code == 403          # no orders for the head doctor
