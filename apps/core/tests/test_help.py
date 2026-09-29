"""S5-04: one-page role instructions — web page and printable PDF."""
import pytest

from apps.accounts.models import Role

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("role", Role.values)
def test_own_role_page_by_default(login, role):
    response = login(role).get("/help/")
    assert response.status_code == 200
    assert response.context["role"] == role
    assert Role(role).label in response.content.decode()


def test_nurse_page_names_the_real_buttons_and_deadline(login, settings):
    settings.ORDER_DEADLINE = "16:00"
    html = login(Role.DEPT_NURSE).get("/help/").content.decode()
    for label in ("Внести порції", "Зберегти чернетку", "Подати повторно", "до <b>16:00</b>"):
        assert label in html


def test_any_role_can_read_other_roles_instructions(login):
    """Deliberately open to all staff: the dietician explains the nurse's steps, the admin trains everyone."""
    client = login(Role.DEPT_NURSE)
    response = client.get(f"/help/{Role.ACCOUNTANT}/")
    assert response.status_code == 200
    assert response.context["role"] == Role.ACCOUNTANT


def test_unknown_role_is_404(login):
    client = login(Role.ADMIN)
    assert client.get("/help/superuser/").status_code == 404
    assert client.get("/help/superuser.pdf").status_code == 404


def test_anonymous_is_sent_to_login(client):
    for url in ("/help/", f"/help/{Role.CHEF}/", f"/help/{Role.CHEF}.pdf"):
        response = client.get(url)
        assert response.status_code == 302
        assert response["Location"].startswith("/login/")


@pytest.mark.parametrize("role", Role.values)
def test_pdf_served(login, role):
    response = login(Role.DIETICIAN).get(f"/help/{role}.pdf")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


@pytest.mark.parametrize("role", Role.values)
def test_each_instruction_fits_one_a4_page(role):
    """The ticket's promise: one printed page per role. Fails when a text grows too long."""
    from django.template.loader import render_to_string
    from weasyprint import HTML

    from apps.core.views import _help_context

    document = HTML(string=render_to_string("help/print.html", _help_context(role))).render()
    assert len(document.pages) == 1


def test_sidebar_links_to_instructions_for_every_role(login):
    for role in Role.values:
        assert 'href="/help/"' in login(role).get("/").content.decode()


@pytest.mark.parametrize("role", Role.values)
def test_kontur_diagrams_open_for_every_role_and_linked_from_instructions(login, role, settings):
    settings.HOSPITAL_NAME = "КНП ОМР «ОХТИРСЬКА ЦРЛ»"
    client = login(role)
    assert "/help/kontur/" in client.get("/help/").content.decode()
    html = client.get("/help/kontur/").content.decode()
    assert "Контур дня" in html and html.count("<svg") >= 6
    assert "fonts.googleapis.com" not in html  # fonts are vendored, the page works offline
    assert "ОХТИРСЬКА ЦРЛ" in html and 'href="/help/"' in html


def test_kontur_needs_login(client):
    response = client.get("/help/kontur/")
    assert response.status_code == 302 and response["Location"].startswith("/login/")
