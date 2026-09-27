"""Run the checks, record findings, open a review task, ask the model for suggestions (optional)."""
import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.workflow import services as wf
from apps.workflow.models import OPEN_FOR_MAKER

from .checks import run_all
from .definitions import CATALOG_REVIEW
from .models import CheckRun, Finding

log = logging.getLogger(__name__)


class AlreadyReviewed(Exception):
    pass

AI_FEATURE = "catalog_check"
AI_SYSTEM = (
    "Ти помічник дієтсестри лікарняного харчоблоку (Україна, Наказ МОЗ №931). Тобі дають список "
    "знахідок автоматичної перевірки довідників: меню, картки-розкладки, продукти. Для кожної знахідки "
    "запропонуй коротко (1–2 речення українською), що дієтсестрі варто перевірити або виправити. "
    "Не вигадуй чисел: не пропонуй конкретних грамів, ккал чи БЖУ, яких немає у знахідці. "
    "Якщо знахідка схожа на норму (наприклад, особливість дієти), так і скажи. "
    "Також дай загальне резюме на 2–3 речення: що важливо зробити першим."
)
AI_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"finding": {"type": "integer"}, "suggestion": {"type": "string"}},
                "required": ["finding", "suggestion"],
            },
        },
    },
    "required": ["summary", "suggestions"],
}


def run_checks(today=None, *, force=False, use_ai=True):
    """One run per day. Returns (run, created). No findings → run recorded, no task."""
    today = today or timezone.localdate()
    existing = CheckRun.objects.filter(run_date=today).first()
    if existing and not force:
        return existing, False
    if existing and existing.task and existing.task.status not in OPEN_FOR_MAKER:
        raise AlreadyReviewed(f"Перевірку за {today:%d.%m.%Y} вже подано або затверджено — повторіть завтра.")
    drafts = run_all()
    with transaction.atomic():
        run = CheckRun.objects.create(run_date=today)
        Finding.objects.bulk_create([Finding(run=run, **vars(d)) for d in drafts])
    if not drafts:
        return run, True
    if use_ai:
        add_ai_suggestions(run)
    open_review(run)
    return run, True


def open_review(run):
    from apps.accounts.models import Role
    from apps.workflow import notify

    task, created = wf.ensure_task(CATALOG_REVIEW, run.run_date, subject=run)
    with transaction.atomic():
        # A forced re-run the same day takes over the day's (still open) task from the earlier run.
        CheckRun.objects.filter(task=task).exclude(pk=run.pk).update(task=None)
        run.task = task
        run.save(update_fields=["task"])
        if task.object_id != run.pk:
            task.subject = run
            task.save(update_fields=["content_type", "object_id"])
    if created:
        counts = {s: run.findings.filter(severity=s).count() for s in Finding.Severity.values}
        body = (f"Помилок: {counts['bad']}, попереджень: {counts['warn']}, до відома: {counts['info']}."
                + (" Є пропозиції ШІ." if run.ai_status == CheckRun.AIStatus.OK else ""))
        # Admins normally aren't notified per role; a check run is exactly what they want to see.
        notify.notify(notify.users_with_role(Role.ADMIN), task, f"Перевірка довідників: {run.findings.count()} знахідок", body)
    return task


def add_ai_suggestions(run):
    """Best effort. Findings stand on their own; the model only adds text next to them."""
    from apps.assist import client

    if not client.is_available():
        return
    findings = list(run.findings.all())
    text = "\n".join(f"{i}. [{f.get_severity_display()}] {f.title}. {f.detail}" for i, f in enumerate(findings, 1))
    try:
        data = client.extract(
            user=None, feature=AI_FEATURE, system_prompt=AI_SYSTEM, tool_name="suggest", tool_schema=AI_SCHEMA,
            content_blocks=[{"type": "text", "text": text}], request_meta={"findings": len(findings)},
        )
    except (client.AIError, client.AILimitExceeded, client.AIUnavailable) as exc:
        run.ai_status, run.ai_error = CheckRun.AIStatus.ERROR, str(exc)[:300]
        run.save(update_fields=["ai_status", "ai_error"])
        return
    by_no = {s.get("finding"): (s.get("suggestion") or "").strip() for s in data.get("suggestions", [])}
    for i, f in enumerate(findings, 1):
        if by_no.get(i):
            f.ai_suggestion = by_no[i][:1000]
            f.save(update_fields=["ai_suggestion"])
    from apps.assist.models import AIRequestLog

    run.ai_status = CheckRun.AIStatus.OK
    run.ai_summary = (data.get("summary") or "").strip()[:2000]
    run.ai_log = AIRequestLog.objects.filter(feature=AI_FEATURE).order_by("-pk").first()
    run.save(update_fields=["ai_status", "ai_summary", "ai_log"])


# ------------------------------------------------------------------ review
def editable(run, user):
    return run.task is not None and wf.can(user, run.task, "submit")


@transaction.atomic
def resolve(finding, user, status, comment=""):
    run = CheckRun.objects.select_related("task").get(pk=finding.run_id)    # fresh task status
    if not editable(run, user):
        raise PermissionDenied("Знахідки зараз не можна змінювати.")
    if status not in Finding.Status.values:
        raise ValidationError("Невідомий статус.")
    comment = (comment or "").strip()
    if status == Finding.Status.ACCEPTED and not comment:
        raise ValidationError("Поясніть, чому це норма — коментар обов'язковий.")
    finding.status, finding.comment = status, comment
    finding.resolved_by = user if status != Finding.Status.OPEN else None
    finding.resolved_at = timezone.now() if status != Finding.Status.OPEN else None
    finding.save(update_fields=["status", "comment", "resolved_by", "resolved_at"])
    task = run.task
    if task.status == "pending":
        wf.start(task, user)
    return finding


def validate_review(task) -> list[str]:
    run = CheckRun.objects.filter(task=task).first()
    if run is None:
        return ["Перевірку не знайдено."]
    left = run.findings.filter(status=Finding.Status.OPEN).count()
    return [f"Залишилось не розглянутих знахідок: {left}."] if left else []
