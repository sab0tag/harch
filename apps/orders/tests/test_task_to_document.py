"""S3-05: from a task the maker lands on the editable document; corrections are explained."""
import pytest

from apps.orders.tests.conftest import DAY, as_user
from apps.workflow import services as wf
from apps.workflow.definitions import ORDER_SUBMISSION
from apps.workflow.models import Task

pytestmark = pytest.mark.django_db


@pytest.fixture
def order_task(world):
    task, _ = wf.ensure_task(ORDER_SUBMISSION, DAY, department=world.depts["Терапевтичне"])
    return task


def doc_url(world):
    return f"/orders/{world.depts['Терапевтичне'].pk}/{DAY.isoformat()}/"


def test_task_card_sends_nurse_to_order_form_instead_of_bare_submit(client, world, order_task):
    html = as_user(client, world.nurse["Терапевтичне"]).get(f"/tasks/{order_task.pk}/").content.decode()
    assert "Внести порції" in html and doc_url(world) in html
    assert f"/tasks/{order_task.pk}/submit/" not in html  # submitting happens on the document


def test_my_tasks_links_straight_to_the_document(client, world, order_task):
    html = as_user(client, world.nurse["Терапевтичне"]).get("/tasks/?tab=todo").content.decode()
    assert doc_url(world) in html and "Внести порції" in html


def test_submitted_order_explains_how_to_get_it_back(client, world, order_task):
    nurse = world.nurse["Терапевтичне"]
    c = as_user(client, nurse)
    c.get(doc_url(world))  # opens the order and links it to the task
    diet_pk = world.depts["Терапевтичне"].default_diet_id
    c.post(doc_url(world), {"action": "submit", f"diet_{diet_pk}": "10"})
    assert Task.objects.get(pk=order_task.pk).status == "submitted"
    assert "Попросіть дієтсестру повернути" in c.get(doc_url(world)).content.decode()
    assert "повернув(ла) задачу" in c.get(f"/tasks/{order_task.pk}/").content.decode()


def test_checker_sees_return_hint_and_document_link(client, world, order_task):
    nurse = world.nurse["Терапевтичне"]
    c = as_user(client, nurse)
    c.get(doc_url(world))
    diet_pk = world.depts["Терапевтичне"].default_diet_id
    c.post(doc_url(world), {"action": "submit", f"diet_{diet_pk}": "10"})
    html = as_user(client, world.dietician).get(f"/tasks/{order_task.pk}/").content.decode()
    assert "Переглянути документ" in html and "натисніть «Повернути»" in html


def test_other_departments_nurse_still_gets_404(client, world, order_task):
    assert as_user(client, world.nurse["Хірургічне"]).get(f"/tasks/{order_task.pk}/").status_code == 404
