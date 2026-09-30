"""AI-10: «Технології з ШІ» — proposals for cards without technology; nothing written until a person accepts."""
import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import Role
from apps.assist import client as ai
from apps.assist.models import AIDraft, AIRequestLog
from apps.catalog import technology
from apps.catalog.models import Dish

pytestmark = pytest.mark.django_db


@pytest.fixture
def ai_on(settings, monkeypatch):
    settings.AI_ENABLED = True
    monkeypatch.setenv(ai.KEY_ENV, "sk-ant-test-not-a-key")


def fake_model(calls=None, skip=()):
    """Answers for every dish_id it is shown (except `skip`), plus junk the code must drop."""
    def transport(*, content, **kw):
        text = content[0]["text"]
        ids = [int(line.split(": ")[1]) for line in text.splitlines() if line.startswith("dish_id: ")]
        if calls is not None:
            calls.append(ids)
        cards = [{"dish_id": i, "technology": f"Технологія для {i}. Варити до готовності.", "warnings": []}
                 for i in ids if i not in skip]
        cards += [{"dish_id": 999999, "technology": "чужа", "warnings": []},      # not asked → dropped
                  {"dish_id": ids[0], "technology": "повтор", "warnings": []}]     # repeat → dropped
        return ai.Reply({"cards": cards}, 800, 400)
    return transport


def test_generate_makes_a_draft_and_writes_nothing(seeded, ai_on, make_user):
    user = make_user(Role.DIETICIAN)
    calls = []
    with ai.use_transport(fake_model(calls)):
        draft = technology.generate(user)
    ids = draft.input["dish_ids"]
    assert len(ids) == technology.BATCH and len(calls) == 1
    assert set(draft.result["items"]) == {str(i) for i in ids}            # junk ids dropped
    assert draft.result["items"][str(ids[0])]["technology"].startswith("Технологія для")
    assert not Dish.objects.filter(pk__in=ids).exclude(technology="").exists()
    assert AIRequestLog.objects.filter(feature="dish_technology", status="ok").count() == 1


def test_model_sees_card_composition_and_diets(seeded, ai_on, make_user):
    seen = {}

    def transport(**kw):
        seen["text"] = kw["content"][0]["text"]
        return fake_model()(**kw)

    with ai.use_transport(transport):
        draft = technology.generate(make_user(Role.DIETICIAN))
    dish = Dish.objects.get(pk=draft.input["dish_ids"][0])
    ing = dish.ingredients.select_related("product").first()
    assert dish.name in seen["text"] and ing.product.name in seen["text"] and "Дієти:" in seen["text"]


def test_pending_cards_are_not_proposed_twice_and_missing_answers_come_back(seeded, ai_on, make_user):
    user = make_user(Role.DIETICIAN)
    first_ids = list(technology.candidates().values_list("pk", flat=True)[:technology.BATCH])
    with ai.use_transport(fake_model(skip={first_ids[-1]})):
        first = technology.generate(user)
    with ai.use_transport(fake_model()):
        second = technology.generate(user)
    assert not set(second.result["items"]) & set(first.result["items"])
    assert str(first_ids[-1]) in second.result["items"]                   # unanswered card is offered again


def test_accept_writes_edited_text_with_history_and_unverifies(seeded, ai_on, make_user):
    user = make_user(Role.DIETICIAN)
    with ai.use_transport(fake_model()):
        draft = technology.generate(user)
    a, b, c = draft.input["dish_ids"][:3]
    Dish.objects.filter(pk=a).update(verified=True)
    written, skipped = technology.decide(draft, user, {a: "Виправлений текст.", b: "Текст Б."}, {c})
    dish = Dish.objects.get(pk=a)
    assert dish.technology == "Виправлений текст." and dish.verified is False
    last = dish.history.first()
    assert "ШІ" in last.history_change_reason and last.technology == "Виправлений текст."
    assert Dish.objects.get(pk=c).technology == ""
    draft.refresh_from_db()
    assert draft.result["decisions"] == {str(a): "accepted", str(b): "accepted", str(c): "discarded"}
    assert draft.status == AIDraft.Status.READY                          # 7 cards still undecided


def test_never_overwrites_what_a_person_filled_meanwhile(seeded, ai_on, make_user):
    user = make_user(Role.DIETICIAN)
    with ai.use_transport(fake_model()):
        draft = technology.generate(user)
    a = draft.input["dish_ids"][0]
    Dish.objects.filter(pk=a).update(technology="Від шефа.")
    written, skipped = technology.decide(draft, user, {a: "Від ШІ."}, set())
    assert not written and [d.pk for d in skipped] == [a]
    assert Dish.objects.get(pk=a).technology == "Від шефа."


def test_draft_closes_when_everything_is_decided(seeded, ai_on, make_user):
    user = make_user(Role.DIETICIAN)
    with ai.use_transport(fake_model()):
        draft = technology.generate(user)
    ids = draft.input["dish_ids"]
    technology.decide(draft, user, {}, set(ids))
    draft.refresh_from_db()
    assert draft.status == AIDraft.Status.DISCARDED
    with pytest.raises(Exception):
        technology.decide(draft, user, {ids[0]: "пізно"}, set())


def test_screens_happy_path(seeded, ai_on, login):
    c = login(Role.DIETICIAN)
    assert "Технології з ШІ" in c.get("/catalog/dishes/").content.decode()
    with ai.use_transport(fake_model()):
        r = c.post("/catalog/dishes/technology/")
    draft = AIDraft.objects.get(kind=AIDraft.Kind.TECHNOLOGY)
    assert r.url == f"/catalog/dishes/technology/{draft.pk}/"
    html = c.get(r.url).content.decode()
    assert "Запропоновано ШІ" in html and "Технологія для" in html
    a = draft.input["dish_ids"][0]
    c.post(r.url, {"action": "accept", f"pick_{a}": "1", f"text_{a}": "Відварити на пару."})
    assert Dish.objects.get(pk=a).technology == "Відварити на пару."


def test_screens_access(seeded, ai_on, login, make_user):
    with ai.use_transport(fake_model()):
        draft = technology.generate(make_user(Role.DIETICIAN))
    a = draft.input["dish_ids"][0]
    for role in (Role.CHEF, Role.ACCOUNTANT, Role.DEPT_NURSE):
        c = login(role)
        assert c.get("/catalog/dishes/technology/").status_code == 403
        assert c.post(f"/catalog/dishes/technology/{draft.pk}/",
                      {"action": "accept", f"pick_{a}": "1", f"text_{a}": "x"}).status_code == 403
    assert Dish.objects.get(pk=a).technology == ""
    # another dietician may finish the batch: it is shared catalog work, not a personal draft
    other = login(Role.DIETICIAN)
    assert other.get(f"/catalog/dishes/technology/{draft.pk}/").status_code == 200


def test_ai_off_shows_message_and_creates_nothing(seeded, login):
    c = login(Role.DIETICIAN)
    r = c.post("/catalog/dishes/technology/", follow=True)
    assert "ШІ вимкнено" in r.content.decode() and not AIDraft.objects.exists()


def test_command_proposes_in_batches(seeded, ai_on):
    calls = []
    with ai.use_transport(fake_model(calls)):
        call_command("draft_technology", "--limit", "25", stdout=open("/dev/null", "w"))
    assert [len(c) for c in calls] == [10, 10, 5]
    assert AIDraft.objects.filter(kind=AIDraft.Kind.TECHNOLOGY).count() == 3
    assert not Dish.objects.exclude(technology="").exists()


def test_command_refuses_without_ai(seeded):
    with pytest.raises(CommandError):
        call_command("draft_technology", stdout=open("/dev/null", "w"))
