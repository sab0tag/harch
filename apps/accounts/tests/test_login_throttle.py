"""S6-03: password guessing is throttled per login + address, never for the whole hospital IP."""
import pytest
from django.core.management import call_command

from apps.accounts.models import Role, User

pytestmark = pytest.mark.django_db
GOOD = "Kharch-2026!"


@pytest.fixture
def nurse():
    return User.objects.create_user("nurse_ter", password=GOOD, role=Role.DIETICIAN)


def sign_in(client, username, password):
    return client.post("/login/", {"username": username, "password": password})


def fail(client, username, times):
    """Wrong password `times` times; the 5th failure already answers with the lockout page."""
    for attempt in range(1, times + 1):
        assert sign_in(client, username, "wrong").status_code == (429 if attempt >= 5 else 200)


def test_five_failures_lock_that_login_even_with_the_right_password(client, nurse):
    fail(client, "nurse_ter", 5)
    response = sign_in(client, "nurse_ter", GOOD)
    assert response.status_code == 429
    assert "Вхід тимчасово заблоковано" in response.content.decode()
    assert "_auth_user_id" not in client.session


def test_colleague_on_the_same_hospital_ip_still_signs_in(client, nurse):
    User.objects.create_user("chef", password=GOOD, role=Role.CHEF)
    fail(client, "nurse_ter", 5)
    assert sign_in(client, "chef", GOOD).status_code == 302


def test_successful_sign_in_resets_the_counter(client, nurse):
    fail(client, "nurse_ter", 4)
    assert sign_in(client, "nurse_ter", GOOD).status_code == 302
    client.logout()
    fail(client, "nurse_ter", 4)
    assert sign_in(client, "nurse_ter", GOOD).status_code == 302


def test_admin_can_lift_the_lock(client, nurse):
    fail(client, "nurse_ter", 5)
    call_command("axes_reset_username", "nurse_ter", stdout=open("/dev/null", "w"))
    assert sign_in(client, "nurse_ter", GOOD).status_code == 302
