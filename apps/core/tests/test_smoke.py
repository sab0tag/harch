import pytest
from django.conf import settings


def test_locale_and_timezone():
    assert settings.LANGUAGE_CODE == "uk"
    assert settings.TIME_ZONE == "Europe/Kyiv"
    assert settings.USE_TZ is True


@pytest.mark.django_db
def test_healthz_reports_db_ok(client):
    response = client.get("/healthz/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


def test_admin_login_page_renders(client):
    response = client.get("/admin/login/")
    assert response.status_code == 200
