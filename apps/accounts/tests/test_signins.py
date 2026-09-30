"""Sign-in log: «Журнал подій → Входи в систему», new-address flags and alerts to the admins."""
import pytest

from apps.accounts import signins
from apps.accounts.models import Role, User
from apps.accounts.tests.factories import DepartmentFactory
from apps.workflow.models import Notification

pytestmark = pytest.mark.django_db
PW = "Kvitka-Sumy-2026"
CHROME_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
ANDROID = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"


def sign_in(client, username, ip, ua=CHROME_WIN, password=PW):
    r = client.post("/login/", {"username": username, "password": password}, REMOTE_ADDR=ip, HTTP_USER_AGENT=ua)
    client.logout()
    return r


@pytest.fixture
def people():
    return {"admin": User.objects.create_user("boss", password=PW, role=Role.ADMIN),
            "nurse": User.objects.create_user("koval.m", password=PW, role=Role.DEPT_NURSE,
                                              department=DepartmentFactory())}


def test_the_log_flags_new_addresses_and_failures(client, people, settings):
    settings.HOSPITAL_NETWORKS = ["10.0.0.0/8"]
    sign_in(client, "koval.m", "10.1.1.1")
    sign_in(client, "koval.m", "10.1.1.1")
    sign_in(client, "koval.m", "8.8.8.8", ua=ANDROID)
    sign_in(client, "koval.m", "8.8.8.8", password="wrong")
    rows = signins.rows()
    assert [r["kind"] for r in rows] == ["failed", "ok", "ok", "ok"]
    newest_ok = rows[1]
    assert newest_ok["ip"] == "8.8.8.8" and newest_ok["new_address"] and newest_ok["outside"]
    assert newest_ok["device"] == "Chrome · Android"
    assert not rows[2]["new_address"] and not rows[2]["outside"]          # the same office computer again
    assert rows[3]["new_address"]                                          # the very first sign-in
    # flagged: a wrong password from outside the hospital is exactly what to look at
    assert [r["kind"] for r in signins.rows(only_flagged=True)] == ["failed", "ok", "ok"]


def test_admins_hear_about_a_new_address_outside_the_hospital(client, people, settings):
    settings.HOSPITAL_NETWORKS = ["10.0.0.0/8"]
    alerts = lambda: Notification.objects.filter(user=people["admin"], title__startswith="Вхід з нової адреси")  # noqa: E731
    sign_in(client, "koval.m", "10.1.1.1")          # first sign-in ever: not news
    sign_in(client, "koval.m", "10.2.2.2")          # another hospital computer: fine
    assert not alerts().exists()
    sign_in(client, "koval.m", "8.8.8.8")           # from outside the hospital
    assert alerts().count() == 1 and "8.8.8.8" in alerts().first().body
    sign_in(client, "koval.m", "8.8.8.8")           # the same address again: no repeat
    assert alerts().count() == 1


def test_an_admin_login_from_any_new_address_is_reported(client, people, settings):
    settings.HOSPITAL_NETWORKS = []
    sign_in(client, "boss", "10.1.1.1")
    sign_in(client, "boss", "10.9.9.9")
    assert Notification.objects.filter(user=people["admin"], title__startswith="Вхід з нової адреси").count() == 1


def test_the_tab_is_for_the_admin(client, people, login):
    sign_in(client, "koval.m", "10.1.1.1")
    client.force_login(people["admin"])
    html = client.get("/tasks/audit/?tab=signins").content.decode()
    assert "Входи в систему" in html and "koval.m" in html and "10.1.1.1" in html
    assert login(Role.DIETICIAN).get("/tasks/audit/?tab=signins").status_code == 403
