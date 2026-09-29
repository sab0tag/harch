"""S6-06: the training copy — banner, demo sign-ins, and a nightly reset that gives every role work."""
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.accounts.models import User
from apps.core.management.commands import reset_demo
from apps.workflow.definitions import COOKING, ORDER_SUBMISSION, WRITE_OFF
from apps.workflow import services as wf

pytestmark = pytest.mark.django_db


def test_real_system_shows_no_training_marks(client, settings):
    settings.DEMO_MODE = False
    html = client.get("/login/").content.decode()
    assert "НАВЧАЛЬНА ВЕРСІЯ" not in html and "demo12345" not in html


def test_training_copy_is_unmistakable(client, login, settings):
    settings.DEMO_MODE = True
    html = client.get("/login/").content.decode()
    assert "НАВЧАЛЬНА ВЕРСІЯ" in html and "demo12345" in html and "<title>[Навчання]" in html
    from apps.accounts.models import Role
    from apps.accounts.tests.factories import DepartmentFactory, UserFactory

    UserFactory(username="nurse-surgery", role=Role.DEPT_NURSE, department=DepartmentFactory(name="Хірургічне"))
    surgery = User.objects.get(username="nurse-surgery").department
    assert "<code>nurse-surgery</code></td><td>Хірургічне</td>" in client.get("/login/").content.decode()
    from apps.orders.services import open_order

    open_order(surgery, timezone.localdate() + timedelta(days=1))           # an order waiting for this nurse
    assert "Хірургічне <span class=\"badge text-bg-success\">є що ввести" in client.get("/login/").content.decode()
    assert ">Навчання</span>" in login("dietician").get("/").content.decode()  # the badge stays in the top bar


@pytest.mark.parametrize("demo_mode, db_name", [(False, "harch_demo"), (True, "harch")])
def test_reset_refuses_anywhere_but_a_demo_database(settings, monkeypatch, demo_mode, db_name):
    settings.DEMO_MODE = demo_mode
    monkeypatch.setattr(reset_demo, "database_name", lambda: db_name)
    User.objects.create_user("keep_me", role="admin")
    with pytest.raises(CommandError, match="wipes the database"):
        call_command("reset_demo")
    assert User.objects.filter(username="keep_me").exists()


@pytest.mark.django_db(transaction=True)  # flush = TRUNCATE, as in the real nightly run
def test_reset_gives_every_role_something_to_do(settings, monkeypatch):
    settings.DEMO_MODE = True
    monkeypatch.setattr(reset_demo, "database_name", lambda: "harch_demo")
    User.objects.create_user("stale", role="admin")
    call_command("reset_demo", days=2, stdout=StringIO())
    assert not User.objects.filter(username="stale").exists()
    people = {u.username: u for u in User.objects.all()}
    today = timezone.localdate()
    tomorrow, yesterday = today + timedelta(days=1), today - timedelta(days=1)

    nurses = [u for u in people.values() if u.username.startswith("nurse-")]
    assert len(nurses) == 9 and len({n.department_id for n in nurses}) == 9   # the hospital's 9 departments
    assert "nurse-rehab" in people and "nurse-cardio" not in people
    assert people["nurse-surgery"].get_full_name() == "Наталія Ткаченко"      # a person, not a job title
    waiting_for_nurse = [n for n in nurses + [people["nurse"]]
                         if any(t.type == ORDER_SUBMISSION and t.service_date == tomorrow for t in wf.todo_for(n))]
    assert people["nurse"] in waiting_for_nurse and len(waiting_for_nurse) >= 5  # a class has orders to enter
    to_review = wf.review_for(people["dietician"])
    assert sum(t.type == ORDER_SUBMISSION and t.service_date == tomorrow for t in to_review) >= 4
    assert any(t.type == COOKING and t.service_date == today for t in wf.todo_for(people["chef"]))
    assert any(t.type == WRITE_OFF and t.service_date == yesterday for t in wf.todo_for(people["accountant"]))
