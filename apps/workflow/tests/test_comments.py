"""«Обговорення»: remarks on a document between its people, kept in the task's history."""
import pytest
from django.core.exceptions import ValidationError

from apps.consumption.tests.conftest import DAY, cooking_day, world  # noqa: F401
from apps.orders.services import open_order
from apps.workflow import services as wf
from apps.workflow.models import Notification, TaskEvent

pytestmark = pytest.mark.django_db


def test_dietician_asks_the_chef_on_the_plan(client, cooking_day):
    w = cooking_day
    task = w.plan.task
    client.force_login(w.dietician)
    r = client.post(f"/tasks/{task.pk}/comment/", {"text": "Галино, перевір меню №9 — замінила кашу",
                                                  "call": ["chef"], "next": f"/plan/{DAY.isoformat()}/"})
    assert r.status_code == 302 and r.url.endswith("#discussion")
    event = task.events.get(kind=TaskEvent.Kind.COMMENT)
    assert event.actor == w.dietician and task.status == event.to_status            # no status change
    assert Notification.objects.filter(user=w.chef, title__startswith="Коментар:").exists()
    assert not Notification.objects.filter(user=w.dietician, title__startswith="Коментар:").exists()
    client.force_login(w.chef)
    html = client.get(f"/plan/{DAY.isoformat()}/").content.decode()
    assert "Обговорення" in html and "замінила кашу" in html
    assert "замінила кашу" in client.get(f"/tasks/{task.pk}/").content.decode()       # in the timeline too


def test_nurse_comment_on_her_order_reaches_the_dietician_not_other_departments(client, world):
    ther = world.depts["Терапевтичне"]
    task = open_order(ther, DAY).task
    wf.comment(task, world.nurse["Терапевтичне"], "Двоє на виписку завтра зранку")
    assert Notification.objects.filter(user=world.dietician, title__startswith="Коментар:").exists()
    assert not Notification.objects.filter(user=world.nurse["Хірургічне"], title__startswith="Коментар:").exists()
    client.force_login(world.nurse["Хірургічне"])
    assert client.post(f"/tasks/{task.pk}/comment/", {"text": "?"}).status_code == 404


def test_empty_or_too_long_is_refused(cooking_day):
    task = cooking_day.plan.task
    with pytest.raises(ValidationError, match="Напишіть"):
        wf.comment(task, cooking_day.dietician, "   ")
    with pytest.raises(ValidationError, match="задовгий"):
        wf.comment(task, cooking_day.dietician, "x" * (wf.COMMENT_MAX + 1))


def test_a_reader_of_the_document_may_join(cooking_day):
    w = cooking_day
    wf.comment(w.plan.task, w.accountant, "Ціни на кефір змінились з 01.10")
    assert Notification.objects.filter(user=w.dietician, title__startswith="Коментар:").exists()


def test_addressees_are_shown_and_in_the_notification(client, cooking_day):
    w = cooking_day
    task = w.plan.task
    event = wf.comment(task, w.chef, "перевірила", call_roles=["dietician", "dietician", "bogus"])
    assert event.addressed_to == "dietician"
    note = Notification.objects.get(user=w.dietician, title__startswith="Коментар:")
    assert "→ Дієтсестра / лікар-дієтолог: перевірила" in note.body
    client.force_login(w.dietician)
    html = client.get(f"/plan/{DAY.isoformat()}/").content.decode()
    assert "→ <span class=\"fw-semibold text-body\">Дієтсестра / лікар-дієтолог</span>" in html


def test_the_order_asks_about_head_counts_not_menus(client, world):
    ther = world.depts["Терапевтичне"]
    open_order(ther, DAY)
    client.force_login(world.nurse["Терапевтичне"])
    html = client.get(f"/orders/{ther.pk}/{DAY.isoformat()}/").content.decode()
    assert "на №9 двоє чи один" in html and "замінила кашу" not in html
