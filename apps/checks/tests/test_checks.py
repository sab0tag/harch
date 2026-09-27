"""AI-09: scheduled reference-data checks — findings from code, optional AI suggestions, review task."""
from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import Role
from apps.assist import client as ai
from apps.assist.models import AIRequestLog
from apps.checks import checks, services
from apps.checks.definitions import CATALOG_REVIEW
from apps.checks.models import CheckRun, Finding
from apps.workflow import services as wf
from apps.workflow.models import Notification, TaskStatus

pytestmark = pytest.mark.django_db
DAY = date(2026, 10, 5)


@pytest.fixture
def run(seeded):
    run, created = services.run_checks(DAY, use_ai=False)
    assert created
    return run


def by_code(run, code):
    return [f for f in run.findings.all() if f.check_code == code]


def test_seed_problems_are_found(run):
    menus = {f.title.split(":")[0]: f for f in by_code(run, "menus_vs_norm")}
    assert "Дієта №9" in menus and menus["Дієта №9"].severity == "bad"
    assert "№221" in menus["Дієта №9"].detail and menus["Дієта №9"].link.startswith("/menus/?diet=")
    energy = by_code(run, "product_energy")
    assert energy and "Індичка" in energy[0].detail          # 104 kcal declared vs ≈198 from БЖУ
    assert by_code(run, "unverified")[0].severity == "info"
    assert run.ai_status == CheckRun.AIStatus.OFF and not any(f.ai_suggestion for f in run.findings.all())


def test_review_task_opened(run):
    task = run.task
    assert task.type == CATALOG_REVIEW and task.maker_role == Role.DIETICIAN and task.checker_role == Role.ADMIN


def test_admin_gets_notification(seeded, make_user):
    admin = make_user(Role.ADMIN)
    dietician = make_user(Role.DIETICIAN)
    run, _ = services.run_checks(DAY, use_ai=False)
    assert Notification.objects.filter(user=admin, task=run.task, title__startswith="Перевірка довідників").exists()
    assert Notification.objects.filter(user=dietician, task=run.task).exists()


def test_one_run_per_day_unless_forced(run):
    again, created = services.run_checks(DAY, use_ai=False)
    assert not created and again == run
    forced, created = services.run_checks(DAY, use_ai=False, force=True)
    assert created and forced != run
    run.refresh_from_db()
    assert forced.task is not None and run.task is None        # the day's open task moves to the re-run
    assert forced.task.object_id == forced.pk


def test_forced_rerun_refused_after_submit(run, make_user):
    for f in run.findings.all():
        services.resolve(f, make_user(Role.DIETICIAN), Finding.Status.FIXED)
    wf.submit(run.task, run.task.maker or make_user(Role.DIETICIAN))
    with pytest.raises(services.AlreadyReviewed):
        services.run_checks(DAY, use_ai=False, force=True)


def test_no_findings_no_task(db, monkeypatch):
    monkeypatch.setattr(checks, "ALL_CHECKS", [])
    run, created = services.run_checks(DAY)
    assert created and run.findings.count() == 0 and run.task is None


def test_energy_check_flags_only_real_mismatch(seeded):
    from apps.catalog.models import Product

    Product.objects.update(protein=10, fat=0, carbs=0, kcal=40)      # 4·10 = 40: consistent
    assert checks.product_energy() == []
    Product.objects.filter(name="Рис").update(kcal=60)
    [draft] = checks.product_energy()
    assert draft.title.startswith("1 продукт") and "Рис: 60 замість ≈40" in draft.detail


def test_review_flow_maker_checker(run, make_user):
    dietician, admin = make_user(Role.DIETICIAN), make_user(Role.ADMIN)
    findings = list(run.findings.all())
    with pytest.raises(ValidationError):
        services.resolve(findings[0], dietician, Finding.Status.ACCEPTED, "")      # «норма» needs a reason
    with pytest.raises(ValidationError):
        wf.submit(run.task, dietician)                                            # open findings left
    for f in findings:
        services.resolve(f, dietician, Finding.Status.ACCEPTED, "Стартові дані, перевіряємо поступово")
    wf.submit(run.task, dietician)
    with pytest.raises(PermissionDenied):
        services.resolve(findings[0], dietician, Finding.Status.OPEN)             # read-only after submit
    assert not wf.can(dietician, run.task, "approve")
    wf.approve(run.task, admin)
    run.task.refresh_from_db()
    assert run.task.status == TaskStatus.APPROVED


def test_screens_and_access(client, run, make_user):
    for role in (Role.CHEF, Role.DEPT_NURSE, Role.ACCOUNTANT):
        client.force_login(make_user(role))
        assert client.get("/checks/").status_code == 403
        assert client.get(f"/checks/{run.pk}/").status_code == 403
    finding = run.findings.first()
    client.force_login(make_user(Role.CHEF))
    assert client.post(f"/checks/{run.pk}/", {"finding": finding.pk, "status": "fixed"}).status_code == 403
    client.force_login(make_user(Role.DIETICIAN))
    assert "Дієта №9" in client.get(f"/checks/{run.pk}/").content.decode()
    client.post(f"/checks/{run.pk}/", {"finding": finding.pk, "status": "fixed", "comment": ""})
    finding.refresh_from_db()
    assert finding.status == "fixed"
    client.force_login(make_user(Role.ADMIN))
    assert client.get("/checks/").status_code == 200


# ---------------------------------------------------------------- AI (fake transport only)
@pytest.fixture
def ai_on(settings, monkeypatch):
    settings.AI_ENABLED = True
    monkeypatch.setenv(ai.KEY_ENV, "sk-ant-test-not-a-key")


def test_ai_adds_labelled_suggestions_only(seeded, ai_on, client, make_user):
    seen = {}

    def fake(*, system, content, tool, max_tokens):
        seen["text"] = content[0]["text"]
        return ai.Reply({"summary": "Спершу меню №9.", "suggestions": [
            {"finding": 1, "suggestion": "Перевірте розмір порцій круп у меню ОВД."}]}, 500, 120)

    with ai.use_transport(fake):
        run, _ = services.run_checks(DAY)
    assert "Дієта ОВД" in seen["text"]                       # the model sees findings, not raw tables
    assert run.ai_status == CheckRun.AIStatus.OK and run.ai_summary == "Спершу меню №9."
    first = run.findings.order_by("pk").first()
    assert first.ai_suggestion.startswith("Перевірте") and first.status == Finding.Status.OPEN
    assert run.ai_log and run.ai_log.feature == services.AI_FEATURE
    client.force_login(make_user(Role.ADMIN))
    html = client.get(f"/checks/{run.pk}/").content.decode()
    assert "Запропоновано ШІ" in html and "Перевірте розмір порцій" in html


def test_ai_failure_does_not_block_the_run(seeded, ai_on):
    def broken(**kwargs):
        raise ai.AIError("Сервіс ШІ не відповів вчасно.")

    with ai.use_transport(broken):
        run, _ = services.run_checks(DAY)
    assert run.ai_status == CheckRun.AIStatus.ERROR and "не відповів" in run.ai_error
    assert run.task is not None and run.findings.count() > 0
    assert AIRequestLog.objects.filter(feature=services.AI_FEATURE, status="error").exists()
