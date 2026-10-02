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


def test_dietician_addresses_the_head_nurse_of_the_ordering_department(client, world):
    ther, surgery = world.depts["Терапевтичне"], world.depts["Хірургічне"]
    task = open_order(ther, DAY).task
    client.force_login(world.dietician)
    html = client.get(f"/orders/{ther.pk}/{DAY.isoformat()}/").content.decode()
    assert "Старша медсестра · Терапевтичне" in html and "Старша медсестра · Хірургічне" not in html
    event = wf.comment(task, world.dietician, "Уточніть: на №9 двоє чи один?",
                       call_roles=[f"dept_nurse:{ther.pk}", f"dept_nurse:{surgery.pk}"])      # another dept: ignored
    assert event.addressed_to == f"dept_nurse:{ther.pk}"
    assert wf.addressee_labels(event) == ["Старша медсестра · Терапевтичне"]
    note = Notification.objects.get(user=world.nurse["Терапевтичне"], title__startswith="Коментар:")
    assert "→ Старша медсестра · Терапевтичне:" in note.body
    assert not Notification.objects.filter(user=world.nurse["Хірургічне"], title__startswith="Коментар:").exists()
    client.force_login(world.nurse["Терапевтичне"])
    html = client.get(f"/orders/{ther.pk}/{DAY.isoformat()}/").content.decode()
    assert "→ <span class=\"fw-semibold text-body\">Старша медсестра · Терапевтичне</span>" in html
    assert 'value="dept_nurse:' not in html                   # she does not address herself


def test_a_transfer_lets_the_sending_department_be_addressed(cooking_day):
    from apps.orders import corrections
    from apps.orders.tests.test_corrections import diet

    w = cooking_day
    c = corrections.open_correction(w.depts["Терапевтичне"], DAY, w.nurse["Терапевтичне"])
    corrections.submit_correction(c, w.nurse["Терапевтичне"], {diet("ОВД").pk: 1}, from_meal="dinner",
                                  reason="transfer", transfer_from=w.depts["Хірургічне"],
                                  transfer_out={diet("ЩД").pk: 1})
    assert set(wf.nurse_departments(c.task)) == {w.depts["Терапевтичне"].pk, w.depts["Хірургічне"].pk}
    wf.comment(c.task, w.dietician, "Хірургія, підтвердіть ЩД", call_roles=[f"dept_nurse:{w.depts['Хірургічне'].pk}"])
    assert Notification.objects.filter(user=w.nurse["Хірургічне"], title__startswith="Коментар:").exists()


# ------------------------------------------------------------------ what the system did on its own is visible
def test_system_steps_are_named_with_their_reason(client, cooking_day):
    from apps.workflow.models import Task, TaskEvent

    w = cooking_day
    created = w.plan.task.events.get(kind=TaskEvent.Kind.CREATED)
    assert created.actor is None and "план меню відкрито автоматично" in created.comment
    cooking = Task.objects.get(type="cooking", service_date=DAY).events.get(kind=TaskEvent.Kind.CREATED)
    assert "кухні відкрито задачу автоматично" in cooking.comment
    client.force_login(w.admin)
    html = client.get("/tasks/audit/?by=system").content.decode()
    assert "Система" in html and "план меню відкрито автоматично" in html
    assert "Олена" not in html and str(w.dietician) not in html.split("<tbody>")[1]   # people filtered out
    assert "Система" in client.get(f"/tasks/{w.plan.task.pk}/").content.decode()


def test_an_order_opened_on_screen_names_who_opened_it(client, world):
    from apps.workflow.models import TaskEvent

    ther = world.depts["Терапевтичне"]
    client.force_login(world.nurse["Терапевтичне"])
    client.get(f"/orders/{ther.pk}/{DAY.isoformat()}/")
    e = TaskEvent.objects.get(task__department=ther, kind=TaskEvent.Kind.CREATED)
    assert e.actor == world.nurse["Терапевтичне"]


def test_seeded_events_are_marked_in_the_timeline(client, cooking_day):
    from apps.workflow.models import TaskEvent

    TaskEvent.objects.update(seeded=True)
    client.force_login(cooking_day.admin)
    assert "навчальні дані" in client.get(f"/tasks/{cooking_day.plan.task.pk}/").content.decode()
