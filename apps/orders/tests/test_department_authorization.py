"""HTTP and service boundaries for department orders (INV-03, INV-08)."""
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.accounts.models import Role
from apps.accounts.tests.factories import UserFactory
from apps.catalog.models import Diet
from apps.menus.models import Menu
from apps.consumption.models import ConsumptionSnapshotLine, ActualConsumption, WriteOff, StoreIssue
from apps.orders import corrections, services
from apps.orders.models import DailyOrder, DailyOrderLine, OrderCorrection, OrderCorrectionLine
from apps.orders.tests.conftest import DAY
from apps.planning.models import DailyPlan, DailyPlanEntry
from apps.workflow import services as wf
from apps.workflow.models import Notification, Task, TaskEvent

pytestmark = pytest.mark.django_db


@pytest.fixture
def boundary():
    owner = UserFactory(role=Role.DEPT_NURSE)
    outsider = UserFactory(role=Role.DEPT_NURSE)
    diet = Diet.objects.create(code="TEST", name="Тестова дієта", kind=Diet.Kind.VARIANT)
    Menu.objects.create(number=9001, diet=diet)
    order = services.open_order(owner.department, DAY, owner)
    services.save_order(order, owner, {diet.pk: 7}, note="Original order")
    return SimpleNamespace(owner=owner, outsider=outsider, diet=diet, order=order)


def state():
    """Include values, not just counts: rejected writes must not replace rows or edit history."""
    return {
        model._meta.label: list(model.objects.order_by("pk").values())
        for model in (DailyOrder, DailyOrderLine, OrderCorrection, OrderCorrectionLine,
                      Task, TaskEvent, Notification, DailyPlan, DailyPlanEntry,
                      ConsumptionSnapshotLine, ActualConsumption, WriteOff, StoreIssue)
    }


def order_url(department):
    return reverse("orders:edit", args=[department.pk, DAY.isoformat()])


def approved_order(b):
    services.submit_order(b.order, b.owner, {b.diet.pk: 7})
    wf.approve(b.order.task, UserFactory(role=Role.DIETICIAN))


def correction(b):
    approved_order(b)
    c = corrections.open_correction(b.owner.department, DAY, b.owner)
    corrections.save_correction(c, b.owner, {b.diet.pk: 2}, from_meal="lunch", reason="admission")
    return c


@pytest.mark.parametrize("exists", [False, True])
@pytest.mark.parametrize("action", ["get", "save", "submit", "reopen"])
def test_foreign_order_http_denied_without_side_effects(client, boundary, exists, action):
    b = boundary
    department = b.owner.department if exists else UserFactory(role=Role.DEPT_NURSE).department
    client.force_login(b.outsider)
    before = state()
    url = order_url(department)
    response = client.get(url) if action == "get" else client.post(url, {
        "action": action, f"diet_{b.diet.pk}": "99", "note": "Tampered",
        "department": b.outsider.department_id, "dept_id": b.outsider.department_id,
    })
    assert response.status_code == 404
    assert state() == before


def test_order_post_cannot_select_another_object_via_hidden_ids(client, boundary):
    b = boundary
    own = services.open_order(b.outsider.department, DAY, b.outsider)
    foreign_before = state()
    client.force_login(b.outsider)
    response = client.post(order_url(b.outsider.department), {
        "action": "save", f"diet_{b.diet.pk}": "11", "department": b.owner.department_id,
        "order_id": b.order.pk, "task_id": b.order.task_id, "dept_id": b.owner.department_id,
    })
    assert response.status_code == 302
    assert services.portions_by_diet(own) == {b.diet.pk: 11}
    assert services.portions_by_diet(b.order) == {b.diet.pk: 7}
    b.order.refresh_from_db()
    assert b.order.note == "Original order"
    assert list(b.order.task.events.order_by("pk").values()) == [
        e for e in foreign_before["workflow.TaskEvent"] if e["task_id"] == b.order.task_id
    ]


@pytest.mark.parametrize("operation", [services.save_order, services.submit_order])
def test_order_write_services_reject_foreign_nurse(boundary, operation):
    b = boundary
    before = state()
    with pytest.raises(PermissionDenied):
        operation(b.order, b.outsider, {b.diet.pk: 99}, note="Tampered")
    assert state() == before


@pytest.mark.parametrize("exists", [False, True])
def test_open_order_service_rejects_foreign_nurse_without_side_effects(boundary, exists):
    b = boundary
    department = b.owner.department if exists else UserFactory(role=Role.DEPT_NURSE).department
    before = state()
    with pytest.raises(PermissionDenied):
        services.open_order(department, DAY, b.outsider)
    assert state() == before


@pytest.mark.parametrize("actor", ["anonymous", "inactive_nurse", "inactive_admin", "dietician", "chef",
                                   "accountant", "storekeeper", "director"])
def test_open_order_rejects_unauthorized_explicit_actors(boundary, actor):
    b = boundary
    if actor == "anonymous":
        user = AnonymousUser()
    elif actor.startswith("inactive_"):
        role = Role.ADMIN if actor == "inactive_admin" else Role.DEPT_NURSE
        user = UserFactory(role=role, department=b.owner.department, is_active=False)
    else:
        user = UserFactory(role=actor)
    before = state()
    with pytest.raises(PermissionDenied):
        services.open_order(b.owner.department, DAY, user)
    assert state() == before


@pytest.mark.parametrize("actor", ["system", "admin", "owner"])
def test_open_order_authorized_creation_is_idempotent(boundary, actor):
    b = boundary
    user = None if actor == "system" else UserFactory(role=Role.ADMIN) if actor == "admin" else b.owner
    day = DAY.replace(day=2)
    order = services.open_order(b.owner.department, day, user)
    before = state()
    assert services.open_order(b.owner.department, day, user).pk == order.pk
    assert state() == before
    assert order.task.events.get(kind=TaskEvent.Kind.CREATED).actor == user


def test_foreign_correction_creation_is_refused_in_http_and_service(client, boundary):
    b = boundary
    approved_order(b)
    client.force_login(b.outsider)
    before = state()
    response = client.post(reverse("orders:correction_new", args=[b.owner.department_id, DAY.isoformat()]))
    # The existing UI reports the service denial then redirects to the inaccessible order.
    assert response.status_code == 302
    assert response.url == order_url(b.owner.department)
    assert client.get(response.url).status_code == 404
    with pytest.raises(PermissionDenied):
        corrections.open_correction(b.owner.department, DAY, b.outsider)
    assert state() == before


@pytest.mark.parametrize("action", ["get", "save", "submit"])
def test_foreign_correction_http_denied_without_side_effects(client, boundary, action):
    b = boundary
    c = correction(b)
    client.force_login(b.outsider)
    before = state()
    url = reverse("orders:correction", args=[c.pk])
    response = client.get(url) if action == "get" else client.post(url, {
        "action": action, f"diet_{b.diet.pk}": "99", "reason": "admission", "from_meal": "lunch",
        "department": b.outsider.department_id, "transfer_from": b.outsider.department_id,
    })
    assert response.status_code == 404
    assert state() == before


@pytest.mark.parametrize("operation", [corrections.save_correction, corrections.submit_correction])
def test_correction_write_services_reject_foreign_nurse(boundary, operation):
    b = boundary
    c = correction(b)
    before = state()
    with pytest.raises(PermissionDenied):
        operation(c, b.outsider, {b.diet.pk: 99}, from_meal="lunch", reason="admission")
    assert state() == before


@pytest.mark.parametrize("kind", ["order", "correction"])
@pytest.mark.parametrize("action", ["view", "comment", "start", "submit", "approve", "reject", "cancel", "recall"])
def test_foreign_task_history_and_actions_denied(client, boundary, kind, action):
    b = boundary
    task = correction(b).task if kind == "correction" else b.order.task
    client.force_login(b.outsider)
    before = state()
    if action == "view":
        response = client.get(reverse("workflow:task_detail", args=[task.pk]))
    elif action == "comment":
        response = client.post(reverse("workflow:task_comment", args=[task.pk]), {"text": "Tampered"})
    else:
        response = client.post(reverse("workflow:task_action", args=[task.pk, action]), {"comment": "Tampered"})
    assert response.status_code == 404
    assert state() == before


def test_foreign_notification_cannot_be_opened_or_marked_read(client, boundary):
    b = boundary
    note = Notification.objects.filter(user=b.owner, task=b.order.task).first()
    assert note is not None
    client.force_login(b.outsider)
    before = state()
    assert client.get(reverse("workflow:notification_open", args=[note.pk])).status_code == 404
    assert state() == before


@pytest.mark.parametrize("action", ["save", "submit"])
def test_transfer_sender_can_read_but_cannot_change_receivers_correction(client, boundary, action):
    b = boundary
    c = correction(b)
    sender_order = services.open_order(b.outsider.department, DAY, b.outsider)
    services.submit_order(sender_order, b.outsider, {b.diet.pk: 7})
    wf.approve(sender_order.task, UserFactory(role=Role.DIETICIAN))
    corrections.save_correction(c, b.owner, {b.diet.pk: 2}, from_meal="lunch", reason="transfer",
                                transfer_from=b.outsider.department)
    client.force_login(b.outsider)
    before = state()
    url = reverse("orders:correction", args=[c.pk])
    response = client.get(url)
    assert response.status_code == 200
    assert response.context["editable"] is False
    response = client.post(url, {"action": action, f"diet_{b.diet.pk}": "99",
                                 "reason": "admission", "from_meal": "lunch"})
    assert response.status_code == 302
    assert state() == before


def test_owner_can_read_and_submit_correction(client, boundary):
    b = boundary
    c = correction(b)
    client.force_login(b.owner)
    url = reverse("orders:correction", args=[c.pk])
    assert client.get(url).status_code == 200
    response = client.post(url, {"action": "submit", f"diet_{b.diet.pk}": "3",
                                 "reason": "admission", "from_meal": "lunch"})
    assert response.status_code == 302
    c.refresh_from_db()
    assert c.task.status == "submitted"
    assert c.lines.get().delta == 3


@pytest.mark.parametrize("name,args", [
    ("orders:summary", []),
    ("planning:plan", [DAY.isoformat()]),
    ("planning:preview", [DAY.isoformat()]),
    ("planning:sign", []),
    ("consumption:kitchen_day", [DAY.isoformat()]),
    ("consumption:dish_card", [DAY.isoformat(), "lunch", 9001]),
    ("consumption:store_day", [DAY.isoformat()]),
    ("consumption:report", []),
    ("consumption:write_off_day", [DAY.isoformat()]),
    ("reports:index", []),
    ("reports:menu_pdf", [DAY.isoformat()]),
    ("reports:brakerazh_pdf", [DAY.isoformat()]),
    ("reports:menu_vymoha", [DAY.isoformat()]),
    ("reports:accumulative", ["2026-10"]),
])
@pytest.mark.parametrize("method", ["get", "post"])
def test_nurse_cannot_reach_day_wide_documents(client, boundary, name, args, method):
    client.force_login(boundary.owner)
    before = state()
    response = getattr(client, method)(reverse(name, args=args), {
        "date": DAY.isoformat(), "action": "force_plan",
    })
    assert response.status_code == 403
    assert state() == before
