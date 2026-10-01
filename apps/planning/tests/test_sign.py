"""The director signs the approved menu-rozkladka in the system."""
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
    assert client.get("/orders/summary/").status_code == 403          # no orders for the director


def test_sign_list_latest_unsigned_first(world):
    from datetime import date

    from django.utils import timezone

    from apps.planning.models import DailyPlan
    from apps.planning.services import to_sign

    now = timezone.now()
    for d in (date(2026, 9, 27), date(2026, 9, 29), date(2026, 9, 28)):
        DailyPlan.objects.create(service_date=d, approved_at=now)
    DailyPlan.objects.filter(service_date=date(2026, 9, 29)).update(signed_at=now)
    assert [p.service_date.day for p in to_sign()] == [28, 27, 29]


def test_signing_is_in_the_directors_history_and_the_task_timeline(client, director, cooking_day):
    from apps.workflow import services as wf

    plan = cooking_day.plan
    sign_plan(plan, director)
    assert plan.task in wf.history_for(director)
    assert plan.task.events.last().kind == "signed" and plan.task.events.last().actor == director
    client.force_login(director)
    html = client.get("/tasks/?tab=history").content.decode()
    assert "План меню на день" in html
    assert "Підписано" in client.get(f"/tasks/{plan.task.pk}/").content.decode()


def test_document_readers_open_its_task_history_and_see_only_their_links(client, director, cooking_day):
    """Crawl of the demo: the accountant and the director got 404 on «Хронологія задачі» and 403 on
    menu and kitchen links from the plan page."""
    w = cooking_day
    plan_task = w.plan.task
    for user in (w.accountant, director):
        client.force_login(user)
        assert client.get(f"/tasks/{plan_task.pk}/").status_code == 200
        html = client.get(f"/plan/{DAY.isoformat()}/").content.decode()
        assert "/menus/" not in html and "/kitchen/" not in html
    client.force_login(w.nurse["Терапевтичне"])
    assert client.get(f"/tasks/{plan_task.pk}/").status_code == 404          # not a reader of the plan
    client.force_login(w.chef)
    html = client.get(f"/plan/{DAY.isoformat()}/").content.decode()
    assert "/menus/" in html and "/kitchen/" in html


def test_previous_stage_link_only_when_it_can_be_opened(client, cooking_day):
    from apps.consumption.services import ensure_actuals
    from apps.core.testing import on_the_day
    from apps.workflow import services as wf
    from apps.workflow.models import Task

    w = cooking_day
    ensure_actuals(w.plan)
    with on_the_day():
        wf.submit(w.cooking, w.chef)
    wf.approve(w.cooking, w.dietician)
    write_off = Task.objects.get(type="write_off", service_date=DAY)
    client.force_login(w.accountant)
    html = client.get(f"/tasks/{write_off.pk}/").content.decode()
    assert "Попередній етап" in html and f"/tasks/{w.cooking.pk}/" not in html     # the kitchen is not hers
    client.force_login(w.dietician)
    assert f"/tasks/{w.cooking.pk}/" in client.get(f"/tasks/{write_off.pk}/").content.decode()
