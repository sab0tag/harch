"""S0-03 acceptance: each role sees exactly its sidebar items (docs/04_ui.md table)."""
import re

import pytest

from apps.accounts.models import Role
from apps.core.navigation import PLACEHOLDERS

pytestmark = pytest.mark.django_db

MAIN = ["Головна", "Мої задачі"]
REF_ALL = ["Картки-розкладки", "Меню", "Продукти", "Дієти", "Відділення", "Перевірка довідника"]
HELP = ["Інструкція"]

# Written out by hand from docs/04_ui.md on purpose — not derived from NAV_GROUPS.
EXPECTED = {
    Role.ADMIN: MAIN + ["Замовлення", "План меню на день", "Сьогодні на кухні", "Розхід продуктів",
                        "Списання", "Звіти", *REF_ALL, "Користувачі та ролі", "Журнал подій", "ШІ-помічник", *HELP],
    Role.DIETICIAN: MAIN + ["Замовлення", "План меню на день", "Сьогодні на кухні", "Розхід продуктів",
                            "Списання", "Звіти", *REF_ALL, *HELP],
    Role.CHEF: MAIN + ["План меню на день", "Сьогодні на кухні", "Розхід продуктів",
                       "Картки-розкладки", "Меню", "Продукти", "Дієти", *HELP],
    Role.DEPT_NURSE: MAIN + ["Замовлення", *HELP],
    Role.ACCOUNTANT: MAIN + ["План меню на день", "Розхід продуктів", "Списання", "Звіти",
                             "Картки-розкладки", "Продукти", *HELP],
}


def sidebar_items(html: str) -> list[str]:
    return re.findall(r'data-nav="([^"]+)"', html)


@pytest.mark.parametrize("role", list(EXPECTED))
def test_sidebar_matches_role(login, role):
    response = login(role).get("/")
    assert response.status_code == 200
    assert sidebar_items(response.content.decode()) == EXPECTED[role]


def test_admin_group_headings_hidden_for_nurse(login):
    html = login(Role.DEPT_NURSE).get("/").content.decode()
    assert "Довідники" not in html
    assert "Адміністрування" not in html


def test_active_item_is_highlighted(login):
    html = login(Role.CHEF).get("/kitchen/").content.decode()
    assert re.search(r'class="nav-link active"[^>]*data-nav="Сьогодні на кухні"', html)


@pytest.mark.parametrize(
    "role,slug,status",
    [
        (Role.DEPT_NURSE, "consumption", 403),   # hidden link must also be a denied URL
        (Role.CHEF, "kitchen", 200),
        (Role.ACCOUNTANT, "write-off", 200),
        (Role.ACCOUNTANT, "kitchen", 403),
        (Role.ADMIN, "reports", 200),
        (Role.CHEF, "reports", 403),
    ],
)
def test_section_urls_enforce_roles(login, role, slug, status):
    from django.urls import reverse

    from apps.core.navigation import ITEMS

    item = ITEMS[slug]
    url = reverse(item.url_name) if item.url_name else f"/s/{slug}/"
    assert login(role).get(url).status_code == status


@pytest.mark.parametrize("slug", sorted(PLACEHOLDERS) or [pytest.param("none", marks=pytest.mark.skip("no placeholders left"))])
def test_every_placeholder_renders_for_admin(login, slug):
    response = login(Role.ADMIN).get(f"/s/{slug}/")
    assert "Розділ у розробці" in response.content.decode()


def test_unknown_section_is_404(login):
    assert login(Role.ADMIN).get("/s/nope/").status_code == 404


def test_anonymous_is_redirected_to_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert response.url.startswith("/login/")


def test_login_page_is_ukrainian(client):
    html = client.get("/login/").content.decode()
    assert 'lang="uk"' in html
    assert "Увійти" in html


def test_login_and_logout_flow(client, make_user):
    make_user(role=Role.CHEF, username="chef1")
    response = client.post("/login/", {"username": "chef1", "password": "pass12345!"})
    assert response.status_code == 302 and response.url == "/"
    assert "Сьогодні на кухні" in client.get("/").content.decode()
    response = client.post("/logout/")
    assert response.status_code == 302
    assert client.get("/").status_code == 302


def test_header_shows_role_and_department(login):
    client = login(Role.DEPT_NURSE)
    html = client.get("/").content.decode()
    assert "Старша медсестра відділення" in html
    assert client.user.department.name in html


def test_mobile_offcanvas_markup_present(login):
    html = login(Role.CHEF).get("/").content.decode()
    assert 'offcanvas-lg' in html and 'data-bs-target="#sidebar"' in html


def test_create_demo_users_refuses_without_debug(settings):
    from django.core.management import CommandError, call_command

    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command("create_demo_users")


def test_create_demo_users_is_idempotent(settings):
    from django.core.management import call_command

    from apps.accounts.models import User

    settings.DEBUG = True
    call_command("create_demo_users")
    call_command("create_demo_users")
    assert User.objects.count() == 5
    assert User.objects.get(username="nurse").department is not None
