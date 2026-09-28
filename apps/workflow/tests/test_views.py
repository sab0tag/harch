"""S2-02 / S2-03: task screens, actions over HTTP, notifications."""
import pytest

from apps.accounts.models import Role
from apps.workflow import services as wf
from apps.workflow.definitions import MENU_PLANNING, ORDER_SUBMISSION
from apps.workflow.models import Notification, Task, TaskStatus
from apps.workflow.tests.conftest import DAY, fill_doc, submit

pytestmark = pytest.mark.django_db


@pytest.fixture
def order(hospital):
    return fill_doc(wf.ensure_task(ORDER_SUBMISSION, DAY, department=hospital.depts[0])[0])


def as_user(client, user):
    client.force_login(user)
    return client


def test_my_tasks_tabs(client, hospital, order):
    html = as_user(client, hospital.nurses[0]).get("/tasks/").content.decode()
    assert "Замовлення відділення" in html and "Терапевтичне" in html
    html = as_user(client, hospital.nurses[1]).get("/tasks/?tab=todo").content.decode()
    assert "Терапевтичне" not in html


def test_task_of_other_department_is_404(client, hospital, order):
    assert as_user(client, hospital.nurses[1]).get(f"/tasks/{order.pk}/").status_code == 404
    assert as_user(client, hospital.nurses[1]).post(f"/tasks/{order.pk}/submit/").status_code == 404


def test_task_not_involving_role_is_404(client, hospital, order):
    assert as_user(client, hospital.accountant).get(f"/tasks/{order.pk}/").status_code == 404


def test_submit_and_approve_over_http(client, hospital, order):
    r = as_user(client, hospital.nurses[0]).post(f"/tasks/{order.pk}/submit/", {"comment": "32 ОВД"}, follow=True)
    assert "Подано на перевірку" in r.content.decode()
    order.refresh_from_db()
    assert order.status == TaskStatus.SUBMITTED
    html = as_user(client, hospital.dietician).get(f"/tasks/{order.pk}/").content.decode()
    assert "Затвердити" in html and "32 ОВД" in html
    as_user(client, hospital.dietician).post(f"/tasks/{order.pk}/approve/")
    order.refresh_from_db()
    assert order.status == TaskStatus.APPROVED


def test_reject_without_comment_shows_error(client, hospital, order):
    submit(order, hospital.nurses[0])
    r = as_user(client, hospital.dietician).post(f"/tasks/{order.pk}/reject/", {"comment": ""}, follow=True)
    assert "коментар обов" in r.content.decode()
    order.refresh_from_db()
    assert order.status == TaskStatus.SUBMITTED


def test_rejection_comment_shown_to_maker(client, hospital, order):
    wf.reject(submit(order, hospital.nurses[0]), hospital.dietician, "Додайте порції №9")
    html = as_user(client, hospital.nurses[0]).get(f"/tasks/{order.pk}/").content.decode()
    assert "Повернуто на доопрацювання" in html and "Додайте порції №9" in html and "Внести порції" in html  # resubmit happens on the document


def test_checker_buttons_hidden_for_own_submission(client, hospital, order):
    submit(order, hospital.admin)
    html = as_user(client, hospital.admin).get(f"/tasks/{order.pk}/").content.decode()
    assert "Затвердити" not in html and "Подано вами" in html


def test_action_by_wrong_role_is_refused(client, hospital, order):
    submit(order, hospital.nurses[0])
    r = as_user(client, hospital.nurses[0]).post(f"/tasks/{order.pk}/approve/", follow=True)
    assert r.status_code == 200
    order.refresh_from_db()
    assert order.status == TaskStatus.SUBMITTED


def test_unknown_action_is_404(client, hospital, order):
    assert as_user(client, hospital.nurses[0]).post(f"/tasks/{order.pk}/explode/").status_code == 404


def test_home_counters(client, hospital, order):
    html = as_user(client, hospital.nurses[0]).get("/").content.decode()
    assert "Мені виконати" in html
    assert as_user(client, hospital.nurses[0]).get("/").context["todo_count"] == 1


def test_audit_admin_only(client, hospital, order):
    assert as_user(client, hospital.admin).get("/tasks/audit/").status_code == 200
    assert as_user(client, hospital.dietician).get("/tasks/audit/").status_code == 403


def test_sidebar_marks_only_audit_active_on_audit_page(client, hospital):
    html = as_user(client, hospital.admin).get("/tasks/audit/").content.decode()
    assert html.count('class="nav-link active"') == 1
    assert 'active" href="/tasks/audit/"' in html


class TestNotifications:
    def test_count_badge(self, client, hospital, order):
        html = as_user(client, hospital.nurses[0]).get("/tasks/notifications/count/").content.decode()
        assert 'id="notification-count">1<' in html
        html = as_user(client, hospital.nurses[1]).get("/tasks/notifications/count/").content.decode()
        assert "notification-count" not in html

    def test_list_and_open_marks_read(self, client, hospital, order):
        client = as_user(client, hospital.nurses[0])
        assert "Нова задача" in client.get("/tasks/notifications/").content.decode()
        note = Notification.objects.get(user=hospital.nurses[0])
        r = client.get(f"/tasks/notifications/{note.pk}/open/")
        # Straight to the document, not to the task card (pilot feedback: the write-off was hard to find).
        assert r.status_code == 302 and r.url == f"/orders/{hospital.depts[0].pk}/{DAY.isoformat()}/"
        note.refresh_from_db()
        assert note.read_at is not None

    def test_cannot_open_someone_elses_notification(self, client, hospital, order):
        note = Notification.objects.get(user=hospital.nurses[0])
        assert as_user(client, hospital.nurses[1]).get(f"/tasks/notifications/{note.pk}/open/").status_code == 404

    def test_read_all(self, client, hospital, order):
        wf.ensure_task(ORDER_SUBMISSION, DAY, department=hospital.depts[1])
        client = as_user(client, hospital.dietician)
        submit(order, hospital.nurses[0])
        assert Notification.objects.filter(user=hospital.dietician, read_at__isnull=True).exists()
        client.post("/tasks/notifications/read-all/", HTTP_HX_REQUEST="true")
        assert not Notification.objects.filter(user=hospital.dietician, read_at__isnull=True).exists()


def test_chain_visible_in_ui(client, hospital):
    for i, d in enumerate(hospital.depts):
        t = wf.ensure_task(ORDER_SUBMISSION, DAY, department=d)[0]
        wf.approve(submit(t, hospital.nurses[i]), hospital.dietician)
    plan = Task.objects.get(type=MENU_PLANNING)
    html = as_user(client, hospital.dietician).get("/tasks/?tab=todo").content.decode()
    assert "План меню на день" in html
    html = as_user(client, hospital.dietician).get(f"/tasks/{plan.pk}/").content.decode()
    assert "Попередній етап" in html


def test_badge_polls_into_itself(client, hospital):
    """Regression: the badge sits inside the bell button whose hx-target is the dropdown;
    without its own hx-target the count was swapped into the dropdown and never shown."""
    html = as_user(client, hospital.chef).get("/").content.decode()
    assert 'id="notification-badge"' in html and 'hx-target="this"' in html


def test_fresh_notification_says_just_now(client, hospital, order):
    assert "щойно" in as_user(client, hospital.nurses[0]).get("/tasks/notifications/").content.decode()


class TestTabOrder:
    def test_review_tab_longest_waiting_first(self, client, hospital):
        from datetime import timedelta

        from django.utils import timezone

        tasks = [fill_doc(wf.ensure_task(ORDER_SUBMISSION, DAY, department=d)[0]) for d in hospital.depts]
        for t, nurse in zip(tasks, hospital.nurses):
            submit(t, nurse)
        now = timezone.now()
        # Same service date for all three, so only the submission time decides the order.
        for t, hours_ago in zip(tasks, (5, 1, 9)):
            Task.objects.filter(pk=t.pk).update(submitted_at=now - timedelta(hours=hours_ago))
        c = as_user(client, hospital.dietician)
        page = c.get("/tasks/?tab=review").context["page"]
        assert [t.pk for t in page] == [tasks[2].pk, tasks[0].pk, tasks[1].pk]
        assert "Подано" in c.get("/tasks/?tab=review").content.decode()

    def test_todo_tab_overdue_on_top_and_marked(self, client, hospital):
        from datetime import timedelta

        from django.utils import timezone

        late = wf.ensure_task(ORDER_SUBMISSION, DAY, department=hospital.depts[0])[0]
        next_day = wf.ensure_task(ORDER_SUBMISSION, DAY + timedelta(days=1), department=hospital.depts[0])[0]
        Task.objects.filter(pk=late.pk).update(deadline=timezone.now() - timedelta(hours=2))
        Task.objects.filter(pk=next_day.pk).update(deadline=timezone.now() + timedelta(hours=20))
        c = as_user(client, hospital.nurses[0])
        page = c.get("/tasks/?tab=todo").context["page"]
        assert [t.pk for t in page][:2] == [late.pk, next_day.pk]
        assert "прострочено" in c.get("/tasks/?tab=todo").content.decode()
