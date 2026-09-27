"""AI-09: reference audit — rules in code, AI only groups duplicate names, humans decide."""
from decimal import Decimal

import pytest
from django.core.management import call_command

from apps.accounts.models import Role
from apps.assist import client
from apps.catalog.audit import check_duplicates, run_audit
from apps.catalog.models import Dish, Product, ReferenceFinding
from apps.workflow.models import Notification

pytestmark = pytest.mark.django_db


def kinds(**filters):
    return set(ReferenceFinding.objects.filter(**filters).values_list("kind", flat=True))


def test_seed_audit_finds_known_problems(seeded):
    run_audit()
    turkey = ReferenceFinding.objects.get(kind="product_kcal", title="Індичка")
    assert "198" in turkey.message and turkey.status == "open"
    assert {"menu_norm", "dish_output", "dish_no_technology"} <= kinds()


def test_fixing_resolves_and_dismissed_stays_dismissed(seeded, make_user):
    run_audit()
    turkey = Product.objects.get(name="Індичка")
    turkey.kcal = Decimal("198")
    turkey.save()
    f = ReferenceFinding.objects.get(kind="dish_output", title="Каша гречана")
    f.status = "dismissed"
    f.save()
    counts = run_audit()
    assert ReferenceFinding.objects.get(kind="product_kcal", obj_id=turkey.pk).status == "resolved"
    assert ReferenceFinding.objects.get(pk=f.pk).status == "dismissed"
    assert counts["resolved"] >= 1


def test_problem_coming_back_reopens(seeded):
    run_audit()
    dish = Dish.objects.get(name="Каша гречана")
    dish.technology = "Крупу промити, варити..."
    dish.save()
    run_audit()
    assert ReferenceFinding.objects.get(kind="dish_no_technology", obj_id=dish.pk).status == "resolved"
    dish.technology = ""
    dish.save()
    assert run_audit()["reopened"] == 1


def test_duplicates_rule_respects_numbers():
    a, b, c = (Product(pk=i, name=n) for i, n in [(1, "Молоко 2.5%"), (2, "Молоко 3.2%"), (3, "молоко  2.5 %")])
    found = check_duplicates([a, b, c])
    assert [(f.details["ids"]) for f in found] == [[1, 3]]


def test_nightly_command_notifies_dieticians_and_admins_once(seeded, make_user):
    diet, admin, chef = make_user(role=Role.DIETICIAN), make_user(role=Role.ADMIN), make_user(role=Role.CHEF)
    call_command("audit_reference", stdout=open("/dev/null", "w"))
    for user in (diet, admin):
        note = Notification.objects.get(user=user)
        assert note.url == "/catalog/audit/" and note.task is None
    assert not Notification.objects.filter(user=chef).exists()
    call_command("audit_reference", stdout=open("/dev/null", "w"))
    assert Notification.objects.filter(user=diet).count() == 1   # nothing new → no new notification


def test_products_without_price_found_and_resolved(seeded):
    run_audit()
    rice = Product.objects.get(name="Рис")
    assert ReferenceFinding.objects.get(kind="product_no_price", obj_id=rice.pk).status == "open"
    rice.price_per_kg = Decimal("55.00")
    rice.save()
    run_audit()
    assert ReferenceFinding.objects.get(kind="product_no_price", obj_id=rice.pk).status == "resolved"


def test_ai_groups_are_validated(seeded, settings, monkeypatch):
    settings.AI_ENABLED = True
    monkeypatch.setenv(client.KEY_ENV, "sk-ant-test")
    ids = dict(Product.objects.values_list("name", "pk"))
    reply = {"groups": [
        {"ids": [ids["Хліб білий"], ids["Хліб білий підсушений"]], "reason": "обидва — білий хліб", "confidence": "high"},
        {"ids": [ids["Рис"], 999999], "reason": "невідомий id", "confidence": "high"},        # unknown id dropped → <2
        {"ids": [ids["Кефір 2.5%"], ids["Молоко 2.5%"]], "reason": "?", "confidence": "low"},  # low ignored
    ]}
    with client.use_transport(lambda **kw: client.Reply(reply, 10, 10)):
        counts = run_audit(with_ai=True)
    assert counts["ai"] == "ok"
    ai = ReferenceFinding.objects.filter(source="ai")
    assert ai.count() == 1 and "Хліб білий підсушений" in ai.get().message


def test_ai_off_audit_still_runs(seeded):
    counts = run_audit(with_ai=True)
    assert counts["ai"] is None and counts["open"] > 0


def test_screen_and_dismiss(client, seeded, login):
    run_audit()
    c = login(Role.DIETICIAN)
    html = c.get("/catalog/audit/").content.decode()
    assert "Ккал не відповідають БЖУ" in html and "Індичка" in html
    f = ReferenceFinding.objects.get(kind="product_kcal", title="Індичка")
    c.post(f"/catalog/audit/{f.pk}/dismiss/", {"note": "за ДСТУ так"})
    f.refresh_from_db()
    assert (f.status, f.note, f.dismissed_by) == ("dismissed", "за ДСТУ так", c.user)


def test_dismiss_needs_a_reason(seeded, login):
    run_audit()
    c = login(Role.DIETICIAN)
    f = ReferenceFinding.objects.get(kind="product_kcal", title="Індичка")
    r = c.post(f"/catalog/audit/{f.pk}/dismiss/", {"note": "  "}, follow=True)
    f.refresh_from_db()
    assert f.status == "open" and "чому це не проблема" in r.content.decode()


def test_dismiss_does_not_redirect_off_site(seeded, login):
    run_audit()
    c = login(Role.DIETICIAN)
    f = ReferenceFinding.objects.get(kind="product_kcal", title="Індичка")
    r = c.post(f"/catalog/audit/{f.pk}/dismiss/", {"note": "за ДСТУ", "back": "https://evil.example/"})
    assert r.status_code == 302 and r.url == "/catalog/audit/"
    r = c.post(f"/catalog/audit/{f.pk}/dismiss/", {"action": "reopen", "back": "/catalog/audit/?status=dismissed"})
    assert r.url == "/catalog/audit/?status=dismissed"


def test_screen_access(login):
    assert login(Role.CHEF).get("/catalog/audit/").status_code == 403
    assert login(Role.DEPT_NURSE).get("/catalog/audit/").status_code == 403
