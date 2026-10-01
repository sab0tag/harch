"""The store marks what it has handed to the kitchen («Видано»)."""
import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import Role
from apps.accounts.tests.factories import UserFactory
from apps.consumption import services
from apps.consumption.models import StoreIssue
from apps.consumption.tests.conftest import DAY, cooking_day, diet, world  # noqa: F401
from apps.orders import corrections
from apps.workflow import services as wf
from apps.workflow.models import Notification

pytestmark = pytest.mark.django_db
URL = f"/store/{DAY.isoformat()}/"


@pytest.fixture
def keeper(db):
    return UserFactory(role=Role.STOREKEEPER)


def test_storekeeper_marks_the_main_requisition_and_the_chef_sees_it(client, keeper, cooking_day):
    w = cooking_day
    client.force_login(keeper)
    assert "Видача кухні" in client.get(URL).content.decode()
    client.post(URL, {"note": "кефір 2.5% замінено на 1%"})
    issue = StoreIssue.objects.get(plan=w.plan, correction=None)
    assert issue.issued_by == keeper and issue.note.startswith("кефір")
    assert Notification.objects.filter(user=w.chef, title__startswith="Склад видав основну вимогу").exists()
    client.force_login(w.chef)
    assert "основна вимога — <span class=\"text-success\">видано" in client.get(f"/kitchen/{DAY.isoformat()}/").content.decode()
    client.force_login(w.admin)
    day = client.get(f"/tasks/day/?date={DAY.isoformat()}").content.decode()
    assert "Видача зі складу" in day and "Видано" in day


def test_each_correction_is_issued_on_its_own(client, keeper, cooking_day):
    w = cooking_day
    nurse = w.nurse["Хірургічне"]
    c = corrections.open_correction(w.depts["Хірургічне"], DAY, nurse)
    corrections.submit_correction(c, nurse, {diet("ЩД").pk: 2}, from_meal="lunch", reason="admission")
    wf.approve(c.task, w.dietician)
    rows = services.store_rows(w.plan)
    assert [r["correction"] for r in rows] == [None, c]
    client.force_login(keeper)
    client.post(URL, {"correction": c.pk})
    assert StoreIssue.objects.filter(plan=w.plan, correction=c).exists()
    assert not StoreIssue.objects.filter(plan=w.plan, correction=None).exists()


def test_twice_is_refused_and_a_mistake_can_be_undone(keeper, cooking_day):
    w = cooking_day
    services.mark_issued(w.plan, keeper)
    with pytest.raises(ValidationError, match="Уже позначено"):
        services.mark_issued(w.plan, keeper)
    with pytest.raises(PermissionDenied):
        services.unmark_issued(w.plan, UserFactory(role=Role.STOREKEEPER))     # someone else's mark
    services.unmark_issued(w.plan, keeper)
    assert not StoreIssue.objects.exists()


@pytest.mark.parametrize("role", [Role.CHEF, Role.DIETICIAN, Role.DIRECTOR])
def test_only_the_store_marks(role, cooking_day):
    with pytest.raises(PermissionDenied):
        services.mark_issued(cooking_day.plan, UserFactory(role=role))
