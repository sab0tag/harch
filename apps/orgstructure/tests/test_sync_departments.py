"""The hospital's real departments (seed/hospital/departments.json) replace the starter list."""
from datetime import date, timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import Role
from apps.accounts.tests.factories import UserFactory
from apps.orgstructure.models import Department
from apps.orgstructure.services import read_departments, sync_departments
from apps.workflow import services as wf
from apps.workflow.definitions import ORDER_SUBMISSION, all_orders_resolved
from apps.workflow.models import Task, TaskStatus

pytestmark = pytest.mark.django_db
TOMORROW = date(2026, 9, 30)   # "today" is pinned to 29.09.2026 (root conftest)
HOSPITAL = ["Терапевтичне", "Хірургічне", "Неврологічне", "Педіатричне", "Гінекологічне", "Травматологічне",
            "Реанімація", "Пологове", "Реабілітаційне"]


def run(*args):
    out = StringIO()
    call_command("sync_departments", *args, stdout=out)
    return out.getvalue()


def test_the_hospital_list_becomes_the_active_departments(seeded):
    out = run()
    active = list(Department.objects.filter(is_active=True).values_list("name", flat=True))
    assert active == HOSPITAL                                                 # in the hospital's order
    assert "Додано: Реабілітаційне" in out and "Вимкнено відділення: Кардіологічне" in out
    defaults = {d.name: d.default_diet.code for d in Department.objects.filter(is_active=True)}
    assert defaults["Хірургічне"] == "ВБД" and defaults["Терапевтичне"] == "ОВД" and defaults["Пологове"] == "ВБД"
    assert Department.objects.get(name="Педіатричне").patient_category == Department.PatientCategory.CHILDREN
    assert "Додано 0, оновлено 0, вимкнено 0" in run()                       # idempotent


def test_switched_off_department_takes_its_nurses_and_open_orders_along(seeded):
    cardio, therapy = Department.objects.get(name="Кардіологічне"), Department.objects.get(name="Терапевтичне")
    nurse = UserFactory(username="nurse-cardio", role=Role.DEPT_NURSE, department=cardio)
    for dept in Department.objects.filter(is_active=True):
        wf.ensure_task(ORDER_SUBMISSION, TOMORROW, department=dept)
    past, _ = wf.ensure_task(ORDER_SUBMISSION, TOMORROW - timedelta(days=3), department=cardio)
    run()
    nurse.refresh_from_db()
    assert not nurse.is_active
    closed = Task.objects.get(type=ORDER_SUBMISSION, service_date=TOMORROW, department=cardio)
    assert closed.status == TaskStatus.CANCELLED and closed.events.last().comment == "Відділення вимкнено"
    assert Task.objects.get(pk=past.pk).status == TaskStatus.PENDING          # past days: the nightly expiry's job
    # the hospital's own departments are untouched; once they all close, the day's plan starts as usual
    live = Task.objects.filter(type=ORDER_SUBMISSION, service_date=TOMORROW, department__is_active=True)
    assert live.count() == 8 and set(live.values_list("status", flat=True)) == {TaskStatus.PENDING}
    # the new department (Реабілітаційне) has no task yet: the plan waits for it too (the scheduler creates it)
    for dept in Department.objects.filter(is_active=True):
        wf.ensure_task(ORDER_SUBMISSION, TOMORROW, department=dept)
    for task in Task.objects.filter(type=ORDER_SUBMISSION, service_date=TOMORROW, department__is_active=True):
        wf.close_by_system(task, "test", run_hook=True)
    assert all_orders_resolved(TOMORROW)          # the plan would start now (here no portions: none needed)
    assert therapy.is_active


def test_dry_run_and_bad_rows(seeded, tmp_path):
    assert "ПЕРЕВІРКА" in run("--dry-run") and Department.objects.filter(name="Кардіологічне", is_active=True).exists()
    assert not Department.objects.filter(name="Реабілітаційне").exists()
    bad = tmp_path / "d.json"
    bad.write_text('[{"name": "X", "default_diet": "ZZZ"}]', encoding="utf-8")
    with pytest.raises(CommandError, match="немає дієти ZZZ"):
        run(str(bad))


def test_the_profile_file_is_what_the_hospital_answered():
    rows = read_departments()
    assert [r["name"] for r in rows] == HOSPITAL
    assert {r["default_diet"] for r in rows} <= {"ОВД", "ВБД", "НКД"}
