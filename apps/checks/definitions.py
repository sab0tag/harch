"""Task type for a check run: the dietician works through the findings, the admin confirms."""
from datetime import timedelta

from apps.accounts.models import Role
from apps.workflow.definitions import _at
from apps.workflow.registry import TaskDefinition, register

CATALOG_REVIEW = "catalog_review"


def _validate(task):
    from .services import validate_review

    return validate_review(task)


def _url(task):
    from django.urls import reverse

    run = getattr(task, "check_run", None)
    return reverse("checks:run", args=[run.pk]) if run else None


register(TaskDefinition(
    type=CATALOG_REVIEW,
    title="Перевірка довідників",
    maker_role=Role.DIETICIAN,
    checker_role=Role.ADMIN,
    deadline=lambda d: _at(d + timedelta(days=7), "16:00"),
    description="Автоматична перевірка знайшла можливі помилки в довідниках. Виправте або позначте «норма» з коментарем.",
    validate_submit=_validate,
    subject_url=_url,
    action_label="Переглянути знахідки",
))
