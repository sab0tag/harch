"""The hospital's real list of departments (seed/hospital/departments.json) applied to the database."""
import json
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.catalog.models import Diet

from .models import Department

HOSPITAL_DEPARTMENTS = Path(settings.BASE_DIR) / "seed" / "hospital" / "departments.json"
CATEGORIES = {"Дорослі": Department.PatientCategory.ADULTS, "Діти": Department.PatientCategory.CHILDREN,
              "Породіллі": Department.PatientCategory.MATERNITY}


@dataclass
class SyncReport:
    created: list = field(default_factory=list)
    updated: list = field(default_factory=list)
    deactivated: list = field(default_factory=list)
    nurses_off: list = field(default_factory=list)
    orders_closed: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def read_departments(path=HOSPITAL_DEPARTMENTS):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sync_departments(rows, *, dry_run=False):
    """Make the active departments exactly `rows` (name, default_diet code, patient_category), in that order.

    Departments not in the list are switched off (history stays), with their head nurses' accounts and
    their open orders for today and later — closed as orders, so the day's plan does not wait for them."""
    from apps.workflow import services as wf
    from apps.workflow.definitions import ORDER_SUBMISSION
    from apps.workflow.models import Task

    report = SyncReport()
    diets = {d.code: d for d in Diet.objects.all()}
    for row in rows:
        if row["default_diet"] not in diets:
            report.errors.append(f"{row['name']}: немає дієти {row['default_diet']}")
        if row.get("patient_category", "Дорослі") not in CATEGORIES:
            report.errors.append(f"{row['name']}: категорія «{row['patient_category']}» — одна з {', '.join(CATEGORIES)}")
    if report.errors:
        return report

    with transaction.atomic():
        wanted = []
        for i, row in enumerate(rows):
            values = {"default_diet": diets[row["default_diet"]], "sort_order": i, "is_active": True,
                      "patient_category": CATEGORIES[row.get("patient_category", "Дорослі")]}
            dept = Department.objects.filter(name__iexact=row["name"]).first()
            if dept is None:
                dept = Department(name=row["name"], **values)
                report.created.append(row["name"])
            elif any(getattr(dept, k) != v for k, v in values.items()):
                for k, v in values.items():
                    setattr(dept, k, v)
                report.updated.append(f"{dept.name}: {values['default_diet'].code}")
            if not dry_run:
                dept.save()
            wanted.append(dept.pk)
        for dept in Department.objects.filter(is_active=True).exclude(pk__in=[pk for pk in wanted if pk]):
            report.deactivated.append(dept.name)
            nurses = User.objects.filter(role=Role.DEPT_NURSE, department=dept, is_active=True)
            report.nurses_off += list(nurses.values_list("username", flat=True))
            orders = Task.objects.open_for_maker().filter(type=ORDER_SUBMISSION, department=dept,
                                                          service_date__gte=timezone.localdate())
            report.orders_closed += [str(t) for t in orders]
            if not dry_run:
                for task in orders:
                    wf.close_by_system(task, "Відділення вимкнено", run_hook=True)
                nurses.update(is_active=False)
                dept.is_active = False
                dept.save(update_fields=["is_active", "updated_at"])
        if dry_run:
            transaction.set_rollback(True)
    return report
