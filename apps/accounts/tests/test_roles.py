import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import Role, User
from apps.accounts.tests.factories import DepartmentFactory

pytestmark = pytest.mark.django_db


def test_dept_nurse_without_department_is_rejected_by_db():
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("nurse", password="x", role=Role.DEPT_NURSE)


def test_dept_nurse_with_department_is_saved():
    user = User.objects.create_user(
        "nurse", password="x", role=Role.DEPT_NURSE, department=DepartmentFactory()
    )
    assert user.pk


def test_user_without_role_is_rejected_by_db():
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("norole", password="x")


def test_createsuperuser_gets_admin_role():
    admin = User.objects.create_superuser("root", "root@example.com", "x")
    assert admin.role == Role.ADMIN


@pytest.mark.parametrize("role", [Role.DIETICIAN, Role.ACCOUNTANT])
def test_has_role_matches_only_own_role(make_user, role):
    user = make_user(role=role)
    assert user.has_role(role)
    assert not user.has_role(Role.CHEF)


def test_admin_passes_every_role_check(make_user):
    assert make_user(role=Role.ADMIN).has_role(Role.CHEF)


@pytest.mark.urls("apps.accounts.tests.urls")
class TestAccessHelpers:
    @pytest.mark.parametrize("url", ["/kitchen/", "/accounting/"])
    def test_anonymous_is_redirected_to_login(self, client, url):
        response = client.get(url)
        assert response.status_code == 302
        assert response.url.startswith("/login/")

    def test_wrong_role_gets_403_on_class_view(self, login):
        assert login(Role.DEPT_NURSE).get("/kitchen/").status_code == 403

    def test_allowed_role_passes_class_view(self, login):
        assert login(Role.CHEF).get("/kitchen/").status_code == 200

    def test_wrong_role_gets_403_on_function_view(self, login):
        assert login(Role.CHEF).get("/accounting/").status_code == 403

    def test_allowed_role_passes_function_view(self, login):
        assert login(Role.ACCOUNTANT).get("/accounting/").status_code == 200

    @pytest.mark.parametrize("url", ["/kitchen/", "/accounting/"])
    def test_admin_passes_everywhere(self, login, url):
        assert login(Role.ADMIN).get(url).status_code == 200
