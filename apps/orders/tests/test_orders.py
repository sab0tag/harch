"""S3-01 / S3-02: department orders and the dietician's summary."""
import pytest

from apps.catalog.models import Diet
from apps.orders.models import DailyOrder
from apps.orders.services import approved_portions
from apps.orders.tests.conftest import DAY, as_user
from apps.workflow import services as wf
from apps.workflow.definitions import MENU_PLANNING, ORDER_SUBMISSION
from apps.workflow.models import Notification, Task, TaskStatus

pytestmark = pytest.mark.django_db
URL = "/orders/{}/2026-10-01/"


def diet(code):
    return Diet.objects.get(code=code)


def post_order(client, dept, action="submit", **portions):
    data = {"action": action}
    for code, value in portions.items():
        data[f"diet_{diet(code.replace('N9', '№9')).pk}"] = value
    return client.post(URL.format(dept.pk), data, follow=True)


# ------------------------------------------------------------------ nurse
def test_nurse_entry_redirects_to_own_department(client, world):
    ther = world.depts["Терапевтичне"]
    r = as_user(client, world.nurse["Терапевтичне"]).get("/orders/?date=2026-10-01")
    assert r.status_code == 302 and r.url == URL.format(ther.pk)


def test_opening_form_creates_order_linked_to_task(client, world):
    ther = world.depts["Терапевтичне"]
    html = as_user(client, world.nurse["Терапевтичне"]).get(URL.format(ther.pk)).content.decode()
    order = DailyOrder.objects.get(department=ther, service_date=DAY)
    assert order.task.type == ORDER_SUBMISSION and order.task.subject == order
    assert "Подати" in html
    assert "№1а" not in html and "№4" not in html  # diets without menus can't be ordered
    assert html.index("ОВД") < html.index("ВБД")    # department default diet first


def test_submit_order(client, world):
    ther = world.depts["Терапевтичне"]
    r = post_order(as_user(client, world.nurse["Терапевтичне"]), ther, ОВД="30", N9="2")
    assert "подано на перевірку" in r.content.decode()
    order = DailyOrder.objects.get(department=ther)
    assert {l.diet.code: l.portions for l in order.lines.all()} == {"ОВД": 30, "№9": 2}
    assert order.task.status == TaskStatus.SUBMITTED
    assert Notification.objects.filter(user=world.dietician, title__startswith="Подано").exists()


def test_save_draft_keeps_task_open(client, world):
    ther = world.depts["Терапевтичне"]
    post_order(as_user(client, world.nurse["Терапевтичне"]), ther, action="save", ОВД="12")
    order = DailyOrder.objects.get(department=ther)
    assert order.task.status == TaskStatus.IN_PROGRESS and order.total_portions == 12


@pytest.mark.parametrize("data,error", [
    ({}, "Вкажіть порції"),
    ({"ОВД": "-3"}, "ціле число"),
    ({"ОВД": "abc"}, "ціле число"),
])
def test_invalid_orders_are_not_submitted(client, world, data, error):
    ther = world.depts["Терапевтичне"]
    html = post_order(as_user(client, world.nurse["Терапевтичне"]), ther, **data).content.decode()
    assert error in html
    assert Task.objects.get(department=ther).status != TaskStatus.SUBMITTED


def test_no_patients(client, world):
    ther = world.depts["Терапевтичне"]
    client = as_user(client, world.nurse["Терапевтичне"])
    html = client.post(URL.format(ther.pk), {"action": "submit", "no_patients": "on",
                                              f"diet_{diet('ОВД').pk}": "5"}, follow=True).content.decode()
    assert "але вказано порції" in html and Task.objects.get(department=ther).status != TaskStatus.SUBMITTED
    client.post(URL.format(ther.pk), {"action": "submit", "no_patients": "on"})
    assert Task.objects.get(department=ther).status == TaskStatus.SUBMITTED


def test_no_patients_mark_switches_off_the_portion_fields(client, world):
    """The form disables portions while «пацієнтів немає» is ticked (the server rejects both anyway)."""
    ther = world.depts["Терапевтичне"]
    client = as_user(client, world.nurse["Терапевтичне"])
    html = client.get(URL.format(ther.pk)).content.decode()
    assert 'id="no-patients-hint" hidden' in html and "field.disabled = box.checked" in html
    client.post(URL.format(ther.pk), {"action": "save", "no_patients": "on"})
    assert 'id="no-patients-hint" hidden' not in client.get(URL.format(ther.pk)).content.decode()


def test_submitted_order_is_read_only(client, world):
    ther = world.depts["Терапевтичне"]
    client = as_user(client, world.nurse["Терапевтичне"])
    post_order(client, ther, ОВД="30")
    html = post_order(client, ther, action="save", ОВД="99").content.decode()
    assert "редагування недоступне" in html
    assert DailyOrder.objects.get(department=ther).total_portions == 30


def test_other_departments_order_is_404(client, world):
    surg = world.depts["Хірургічне"]
    assert as_user(client, world.nurse["Терапевтичне"]).get(URL.format(surg.pk)).status_code == 404


@pytest.mark.parametrize("role", ["chef", "accountant"])
def test_orders_forbidden_for_kitchen_and_accounting(client, world, role):
    assert as_user(client, getattr(world, role)).get("/orders/").status_code == 403


def test_rejected_order_can_be_fixed_and_resubmitted(client, world):
    ther = world.depts["Терапевтичне"]
    nurse = as_user(client, world.nurse["Терапевтичне"])
    post_order(nurse, ther, ОВД="30")
    wf.reject(Task.objects.get(department=ther), world.dietician, "Додайте 2 порції №9")
    html = nurse.get(URL.format(ther.pk)).content.decode()
    assert "Додайте 2 порції №9" in html and "Подати повторно" in html
    post_order(nurse, ther, ОВД="30", N9="2")
    assert Task.objects.get(department=ther).status == TaskStatus.SUBMITTED


# ------------------------------------------------------------------ dietician
def fill_all(client, world):
    values = {"Терапевтичне": {"ОВД": "30"}, "Хірургічне": {"ЩД": "20"}, "Ендокринологічне": {"N9": "8", "ОВД": "2"}}
    for name, portions in values.items():
        post_order(as_user(client, world.nurse[name]), world.depts[name], **portions)


def test_summary_matrix_and_approve_all(client, world):
    fill_all(client, world)
    diet_client = as_user(client, world.dietician)
    r = diet_client.get("/orders/summary/?date=2026-10-01")
    assert r.context["counts"]["submitted"] == 3
    assert r.context["totals"][diet("ОВД").pk] == 32
    diet_client.post("/orders/summary/", {"date": "2026-10-01", "action": "approve_all"})
    assert set(Task.objects.filter(type=ORDER_SUBMISSION).values_list("status", flat=True)) == {TaskStatus.APPROVED}
    assert approved_portions(DAY) == {diet("ОВД").pk: 32, diet("ЩД").pk: 20, diet("№9").pk: 8}
    assert Task.objects.filter(type=MENU_PLANNING, service_date=DAY).exists()


def test_order_the_admin_submitted_is_approved_by_the_dietician(client, world):
    fill_all(client, world)
    Task.objects.filter(department=world.depts["Хірургічне"]).update(submitted_by=world.admin)
    r = as_user(client, world.dietician).get("/orders/summary/?date=2026-10-01")
    assert r.context["counts"]["approvable"] == 3


def test_admin_sees_the_summary_without_approve_buttons(client, world):
    fill_all(client, world)
    r = as_user(client, world.admin).get("/orders/summary/?date=2026-10-01")
    assert r.context["counts"]["submitted"] == 3 and r.context["counts"]["approvable"] == 0
    assert "/approve/" not in r.content.decode()


def test_inline_reject_from_summary_returns_to_summary(client, world):
    fill_all(client, world)
    task = Task.objects.get(department=world.depts["Хірургічне"])
    r = as_user(client, world.dietician).post(
        f"/tasks/{task.pk}/reject/", {"comment": "Перевірте кількість", "next": "/orders/summary/?date=2026-10-01"})
    assert r.url == "/orders/summary/?date=2026-10-01"
    task.refresh_from_db()
    assert task.status == TaskStatus.REJECTED


def test_next_redirect_rejects_foreign_hosts(client, world):
    fill_all(client, world)
    task = Task.objects.get(department=world.depts["Хірургічне"])
    r = as_user(client, world.dietician).post(f"/tasks/{task.pk}/approve/", {"next": "https://evil.example/"})
    assert r.url == f"/tasks/{task.pk}/"


def test_create_missing_tasks(client, world):
    as_user(client, world.dietician).post("/orders/summary/", {"date": "2026-10-01", "action": "create_tasks"})
    assert Task.objects.filter(type=ORDER_SUBMISSION, service_date=DAY).count() == 3


def test_force_plan_uses_only_approved_orders(client, world):
    fill_all(client, world)
    wf.approve(Task.objects.get(department=world.depts["Терапевтичне"]), world.dietician)
    r = as_user(client, world.dietician).post("/orders/summary/", {"date": "2026-10-01", "action": "force_plan"})
    assert r.url == "/plan/2026-10-01/"
    from apps.planning.models import DailyPlan

    plan = DailyPlan.objects.get(service_date=DAY)
    assert {e.diet.code: e.portions for e in plan.entries.all()} == {"ОВД": 30}


def test_summary_forbidden_for_nurse(client, world):
    assert as_user(client, world.nurse["Терапевтичне"]).get("/orders/summary/").status_code == 403


def test_dietician_day_arrows_reach_a_day_without_order(client, world):
    """Bug from the pilot demo: the reviewer stepped «›» to a day the nurse had not opened yet and got a 404."""
    ther = world.depts["Терапевтичне"]
    c = as_user(client, world.dietician)
    r = c.get("/orders/{}/2026-10-02/".format(ther.pk))
    assert r.status_code == 200
    html = r.content.decode()
    assert "ще не відкривало замовлення" in html
    assert "/orders/summary/?date=2026-10-02" in html
    assert 'href="/orders/{}/2026-10-01/"'.format(ther.pk) in html           # «‹» back still works
    assert not DailyOrder.objects.filter(department=ther, service_date="2026-10-02").exists()  # viewing creates nothing
    assert c.post("/orders/{}/2026-10-02/".format(ther.pk), {"action": "submit"}).status_code == 404
    # The other department's nurse still cannot see it.
    assert as_user(client, world.nurse["Хірургічне"]).get("/orders/{}/2026-10-02/".format(ther.pk)).status_code == 404
