"""S2-01: workflow engine — transitions, permissions, chain, notifications, deadlines."""
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection

from apps.workflow import services as wf
from apps.workflow.definitions import COOKING, MENU_PLANNING, ORDER_SUBMISSION, WRITE_OFF
from apps.workflow.models import Notification, Task, TaskEvent, TaskStatus
from apps.workflow.tests.conftest import DAY, submit

pytestmark = pytest.mark.django_db
S = TaskStatus


def orders(h):
    return [wf.ensure_task(ORDER_SUBMISSION, DAY, department=d)[0] for d in h.depts]


def notes(user):
    return list(Notification.objects.filter(user=user).values_list("title", flat=True))


# ----------------------------------------------------------------- creation
def test_ensure_task_is_idempotent_and_logged(hospital):
    t1, created1 = wf.ensure_task(ORDER_SUBMISSION, DAY, department=hospital.depts[0])
    t2, created2 = wf.ensure_task(ORDER_SUBMISSION, DAY, department=hospital.depts[0])
    assert (created1, created2) == (True, False) and t1.pk == t2.pk
    assert list(t1.events.values_list("kind", flat=True)) == ["created"]


def test_new_order_task_notifies_only_that_departments_nurse(hospital):
    wf.ensure_task(ORDER_SUBMISSION, DAY, department=hospital.depts[0])
    assert len(notes(hospital.nurses[0])) == 1
    assert notes(hospital.nurses[1]) == []


def test_per_department_task_requires_department(hospital):
    with pytest.raises(ValueError):
        wf.ensure_task(ORDER_SUBMISSION, DAY)


def test_order_deadline_is_previous_day_16_kyiv(hospital):
    task, _ = wf.ensure_task(ORDER_SUBMISSION, DAY, department=hospital.depts[0])
    assert task.deadline == datetime(2026, 9, 30, 16, 0, tzinfo=ZoneInfo("Europe/Kyiv"))


# ----------------------------------------------------------------- permissions
def test_nurse_cannot_submit_other_departments_order(hospital):
    task = orders(hospital)[1]
    with pytest.raises(PermissionDenied):
        submit(task, hospital.nurses[0])


def test_wrong_role_cannot_submit(hospital):
    with pytest.raises(PermissionDenied):
        submit(orders(hospital)[0], hospital.chef)


def test_submit_from_pending_records_start_and_notifies_checkers(hospital):
    task = submit(orders(hospital)[0], hospital.nurses[0], "30 ОВД")
    assert task.status == S.SUBMITTED and task.submitted_by == hospital.nurses[0]
    assert list(task.events.values_list("kind", flat=True)) == ["created", "started", "submitted"]
    assert any("Подано на перевірку" in t for t in notes(hospital.dietician))
    assert any("Подано на перевірку" in t for t in notes(hospital.dietician2))


def test_approve_pending_task_is_invalid(hospital):
    with pytest.raises(wf.InvalidTransition):
        wf.approve(orders(hospital)[0], hospital.dietician)


def test_maker_cannot_approve_own_submission(hospital):
    """Admin can act as any role, but still never as checker of their own submission."""
    task = submit(orders(hospital)[0], hospital.admin)
    assert not wf.can(hospital.admin, task, "approve")
    with pytest.raises(PermissionDenied):
        wf.approve(task, hospital.admin)
    wf.approve(task, hospital.dietician)


def test_admin_has_no_queue_of_their_own(hospital, client):
    """Order, plan, kitchen and write-off tasks belong to their roles; the admin's «Мені виконати» is empty."""
    task = orders(hospital)[0]
    assert task in wf.todo_for(hospital.nurses[0])
    assert not wf.todo_for(hospital.admin).exists()
    assert wf.can(hospital.admin, task, "submit")          # can still step in on the screens
    client.force_login(hospital.admin)
    assert client.get("/").context["todo_count"] == 0


def test_admin_never_approves_or_returns(hospital):
    """The hospital: the dietician approves orders; the admin sees and cancels, but is not a checker."""
    task = submit(orders(hospital)[0], hospital.nurses[0])
    assert wf.can(hospital.admin, task, "view")
    assert not wf.can(hospital.admin, task, "approve") and not wf.can(hospital.admin, task, "reject")
    with pytest.raises(PermissionDenied):
        wf.approve(task, hospital.admin)
    assert not wf.review_for(hospital.admin).exists()
    assert task in wf.review_for(hospital.dietician)


def test_view_permission(hospital):
    task = orders(hospital)[0]
    assert wf.can(hospital.nurses[0], task, "view")
    assert wf.can(hospital.dietician, task, "view")
    assert not wf.can(hospital.nurses[1], task, "view")
    assert not wf.can(hospital.accountant, task, "view")


# ----------------------------------------------------------------- reject / resubmit / cancel
def test_reject_requires_comment(hospital):
    task = submit(orders(hospital)[0], hospital.nurses[0])
    with pytest.raises(ValidationError):
        wf.reject(task, hospital.dietician, "  ")
    task.refresh_from_db()
    assert task.status == S.SUBMITTED


def test_reject_resubmit_approve(hospital):
    nurse, diet = hospital.nurses[0], hospital.dietician
    task = submit(orders(hospital)[0], nurse)
    task = wf.reject(task, diet, "Не вказано порції для №9")
    assert task.status == S.REJECTED and task.reject_count == 1
    assert task.last_rejection.comment == "Не вказано порції для №9"
    assert any("Повернуто" in t for t in notes(nurse))
    task = submit(task, nurse)
    assert any("Подано повторно" in t for t in notes(diet))
    task = wf.approve(task, diet)
    assert task.status == S.APPROVED and task.resolved_by == diet
    kinds = list(task.events.values_list("kind", flat=True))
    assert kinds == ["created", "started", "submitted", "rejected", "submitted", "approved"]


def test_cancel_admin_only_with_reason(hospital):
    task = orders(hospital)[0]
    with pytest.raises(PermissionDenied):
        wf.cancel(task, hospital.dietician, "дубль")
    with pytest.raises(ValidationError):
        wf.cancel(task, hospital.admin, "")
    assert wf.cancel(task, hospital.admin, "відділення закрите").status == S.CANCELLED


# ----------------------------------------------------------------- chain
def test_menu_planning_appears_only_after_last_order(hospital):
    tasks = orders(hospital)
    for i, t in enumerate(tasks):
        wf.approve(submit(t, hospital.nurses[i]), hospital.dietician)
        exists = Task.objects.filter(type=MENU_PLANNING, service_date=DAY).exists()
        assert exists == (i == len(tasks) - 1)


def test_cancelled_order_counts_as_resolved(hospital):
    t1, t2, t3 = orders(hospital)
    wf.cancel(t3, hospital.admin, "відділення на ремонті")
    wf.approve(submit(t1, hospital.nurses[0]), hospital.dietician)
    wf.approve(submit(t2, hospital.nurses[1]), hospital.dietician)
    assert Task.objects.filter(type=MENU_PLANNING, service_date=DAY).count() == 1


def test_cancelling_last_open_order_starts_planning(hospital):
    t1, t2, t3 = orders(hospital)
    wf.approve(submit(t1, hospital.nurses[0]), hospital.dietician)
    wf.approve(submit(t2, hospital.nurses[1]), hospital.dietician)
    assert not Task.objects.filter(type=MENU_PLANNING).exists()
    wf.cancel(t3, hospital.admin, "немає пацієнтів")
    assert Task.objects.filter(type=MENU_PLANNING, service_date=DAY).count() == 1


def test_full_day_happy_path(hospital):
    h = hospital
    for i, t in enumerate(orders(h)):
        wf.approve(submit(t, h.nurses[i]), h.dietician)
    plan = Task.objects.get(type=MENU_PLANNING, service_date=DAY)
    assert plan.maker_role == "dietician" and plan.checker_role == "chef"
    assert any("План меню" in t for t in notes(h.dietician2))  # every dietician is told
    wf.approve(submit(plan, h.dietician), h.chef)
    cooking = Task.objects.get(type=COOKING, service_date=DAY)
    assert cooking.parent == plan
    wf.approve(submit(cooking, h.chef), h.dietician)
    write_off = Task.objects.get(type=WRITE_OFF, service_date=DAY)
    assert any("Списання" in t for t in notes(h.accountant))
    wf.approve(submit(write_off, h.accountant), h.dietician)
    assert set(Task.objects.values_list("status", flat=True)) == {S.APPROVED}
    assert Task.objects.count() == 6


# ----------------------------------------------------------------- deadlines
def test_overdue_notified_once_to_makers_and_dieticians(hospital):
    task = orders(hospital)[0]
    later = task.deadline + timedelta(minutes=1)
    assert wf.notify_overdue(task.deadline - timedelta(minutes=1)) == []
    assert [t.pk for t in wf.notify_overdue(later) if t.pk == task.pk] == [task.pk]
    assert wf.notify_overdue(later + timedelta(hours=1)) == []
    assert sum("Прострочено" in t for t in notes(hospital.nurses[0])) == 1
    assert sum("Прострочено" in t for t in notes(hospital.dietician)) == 3  # all 3 departments' orders
    assert task.events.filter(kind=TaskEvent.Kind.OVERDUE).count() == 1


def test_submitted_task_is_not_overdue(hospital):
    task = submit(orders(hospital)[0], hospital.nurses[0])
    assert not task.is_overdue(task.deadline + timedelta(hours=1))


# ----------------------------------------------------------------- inboxes
def test_inboxes(hospital):
    t1, t2, _ = orders(hospital)
    submit(t1, hospital.nurses[0])
    assert list(wf.todo_for(hospital.nurses[1])) == [t2]
    assert list(wf.review_for(hospital.dietician)) == [Task.objects.get(pk=t1.pk)]
    assert list(wf.review_for(hospital.chef)) == []
    assert list(wf.history_for(hospital.nurses[0])) == [Task.objects.get(pk=t1.pk)]


# ----------------------------------------------------------------- concurrency
def _in_threads(*calls):
    barrier = threading.Barrier(len(calls))
    errors = []

    def run(fn):
        try:
            barrier.wait()
            fn()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(c,)) for c in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


@pytest.mark.django_db(transaction=True)
def test_parallel_double_approve_creates_next_task_once(hospital):
    for i, t in enumerate(orders(hospital)):
        wf.approve(submit(t, hospital.nurses[i]), hospital.dietician)
    plan = submit(Task.objects.get(type=MENU_PLANNING), hospital.dietician)
    chef2 = type(hospital.chef).objects.create_user("chef2", password="x", role="chef")
    errors = _in_threads(lambda: wf.approve(plan, hospital.chef), lambda: wf.approve(plan, chef2))
    assert len(errors) == 1 and isinstance(errors[0], wf.InvalidTransition)
    assert Task.objects.filter(type=COOKING).count() == 1
    assert Task.objects.get(pk=plan.pk).events.filter(kind="approved").count() == 1


@pytest.mark.django_db(transaction=True)
def test_parallel_last_two_order_approvals_create_one_plan(hospital):
    t1, t2, t3 = orders(hospital)
    wf.approve(submit(t1, hospital.nurses[0]), hospital.dietician)
    submit(t2, hospital.nurses[1])
    submit(t3, hospital.nurses[2])
    errors = _in_threads(lambda: wf.approve(t2, hospital.dietician), lambda: wf.approve(t3, hospital.dietician2))
    assert errors == []
    assert Task.objects.filter(type=MENU_PLANNING, service_date=DAY).count() == 1


# ----------------------------------------------------------------- S7-06 substitution
def test_substitution_login_may_approve_but_the_history_says_so(hospital):
    """The chef covers the dietician with a second login; approving her own order-review is marked."""
    from apps.accounts.models import Role
    from apps.accounts.tests.factories import UserFactory

    chef = hospital.chef
    as_dietician = UserFactory(role=Role.DIETICIAN, main_login=chef, first_name=chef.first_name, last_name=chef.last_name)
    assert chef.is_same_person(as_dietician) and as_dietician.is_same_person(chef)
    assert not hospital.dietician.is_same_person(chef)
    task = submit(orders(hospital)[0], hospital.nurses[0])
    wf.approve(task, as_dietician)                         # an ordinary approval: no mark
    assert "Заміщення" not in task.events.get(kind="approved").comment
    other = orders(hospital)[1]                              # recorded as submitted by her main login
    other.status, other.submitted_by = S.SUBMITTED, chef
    other.save()
    wf.approve(other, as_dietician)                          # the same person, other login: allowed, marked
    event = other.events.get(kind="approved")
    assert event.comment.startswith("Заміщення: затвердила та сама особа")
    assert Notification.objects.filter(user=hospital.admin, title__startswith="Заміщення").exists()
