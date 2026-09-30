"""S6-02: shared department computers — automatic sign-out after inactivity."""
import time

import pytest

from apps.accounts.middleware import LAST_ACTIVITY
from apps.accounts.models import Role

pytestmark = pytest.mark.django_db
BADGE = "/tasks/notifications/count/"


def idle_for(client, minutes):
    session = client.session
    session[LAST_ACTIVITY] = int(time.time()) - minutes * 60
    session.save()


@pytest.fixture(autouse=True)
def _idle_limit(settings):
    settings.SESSION_IDLE_MINUTES = 60


def test_recent_activity_keeps_the_user_signed_in(login):
    client = login(Role.DIETICIAN)
    idle_for(client, 59)
    assert client.get("/").status_code == 200
    assert time.time() - client.session[LAST_ACTIVITY] < 5  # the visit counts as activity


def test_idle_user_is_signed_out_and_told_why(login):
    client = login(Role.DIETICIAN)
    idle_for(client, 61)
    response = client.get("/orders/summary/?date=2026-10-01")
    assert response.status_code == 302
    assert response.url == "/login/?next=/orders/summary/%3Fdate%3D2026-10-01"
    assert "_auth_user_id" not in client.session
    assert "Вихід виконано автоматично: 60 хв без дій" in client.get(response.url).content.decode()


def test_notification_polling_is_not_activity(login):
    client = login(Role.DEPT_NURSE)
    idle_for(client, 50)
    before = client.session[LAST_ACTIVITY]
    assert client.get(BADGE, HTTP_HX_REQUEST="true").status_code == 200
    assert client.session[LAST_ACTIVITY] == before


def test_polling_after_timeout_sends_the_page_to_login(login):
    client = login(Role.DEPT_NURSE)
    idle_for(client, 61)
    response = client.get(BADGE, HTTP_HX_REQUEST="true", HTTP_HX_CURRENT_URL="https://x.test/tasks/?tab=done")
    assert response.status_code == 204
    assert response["HX-Redirect"] == "/login/?next=/tasks/%3Ftab%3Ddone"


def test_session_ends_with_the_browser_and_after_a_shift(settings):
    assert settings.SESSION_EXPIRE_AT_BROWSER_CLOSE is True
    assert settings.SESSION_COOKIE_AGE == 12 * 60 * 60


# ------------------------------------------------------------------ a lost session seen by the bell
def test_bell_without_a_session_sends_the_whole_tab_to_login(client):
    r = client.get(BADGE, HTTP_HX_REQUEST="true", HTTP_HX_CURRENT_URL="https://x.test/orders/summary/?date=2026-10-01")
    assert r.status_code == 204                            # not the login page swapped into the bell
    assert r["HX-Redirect"] == "/login/?next=/orders/summary/%3Fdate%3D2026-10-01"
    assert client.get(BADGE).status_code == 302            # an ordinary (non-htmx) request: the usual redirect


def test_login_never_lands_on_the_bell(client, django_user_model):
    django_user_model.objects.create_user("olena", password="Kvitka-Sumy-2026", role=Role.DIETICIAN)
    r = client.post("/login/?next=/tasks/notifications/count/",
                    {"username": "olena", "password": "Kvitka-Sumy-2026", "next": "/tasks/notifications/count/"})
    assert r.status_code == 302 and r.url == "/"
    client.logout()
    r = client.post("/login/", {"username": "olena", "password": "Kvitka-Sumy-2026", "next": "/orders/summary/"})
    assert r.url == "/orders/summary/"                     # a real page still comes back
