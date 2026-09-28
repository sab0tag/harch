"""AI-01: single entry point, logging, limit, off-switch, key never exposed."""
import pytest
from django.core.management import call_command

from apps.accounts.models import Role
from apps.assist import client
from apps.assist.models import AIRequestLog

pytestmark = pytest.mark.django_db
FAKE_KEY = "sk-ant-test-DO-NOT-LEAK-123"
SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}


@pytest.fixture
def ai_on(settings, monkeypatch):
    settings.AI_ENABLED = True
    settings.AI_DAILY_REQUEST_LIMIT = 3
    monkeypatch.setenv(client.KEY_ENV, FAKE_KEY)


def fake(data=None, error=None):
    calls = []

    def transport(**kwargs):
        calls.append(kwargs)
        if error:
            raise error
        return client.Reply(data or {"name": "Каша"}, 120, 30)

    transport.calls = calls
    return transport


def run(user=None):
    return client.extract(user=user, feature="card_import", system_prompt="s",
                          content_blocks=[{"type": "text", "text": "x"}], tool_schema=SCHEMA,
                          request_meta={"file_kb": 512})


def test_off_by_default_and_nothing_logged():
    assert not client.is_available()
    with pytest.raises(client.AIUnavailable):
        run()
    assert AIRequestLog.objects.count() == 0


def test_enabled_without_key_is_unavailable(settings):
    settings.AI_ENABLED = True
    assert not client.is_available()


def test_successful_call_returns_tool_input_and_logs(ai_on, make_user):
    user = make_user(role=Role.DIETICIAN)
    t = fake()
    with client.use_transport(t):
        assert run(user) == {"name": "Каша"}
    tool = t.calls[0]["tool"]
    assert tool["input_schema"] == SCHEMA and tool["name"] == "record"
    log = AIRequestLog.objects.get()
    assert (log.status, log.feature, log.user, log.input_tokens, log.output_tokens) == ("ok", "card_import", user, 120, 30)
    assert log.request_meta == {"file_kb": 512} and log.duration_ms is not None


def test_error_is_logged_and_user_message_is_safe(ai_on):
    with client.use_transport(fake(error=ValueError(f"boom {FAKE_KEY}"))):
        with pytest.raises(client.AIError) as exc:
            run()
    log = AIRequestLog.objects.get()
    assert log.status == "error" and FAKE_KEY not in log.error and FAKE_KEY not in str(exc.value)


def test_daily_limit_rejects_and_logs(ai_on):
    with client.use_transport(fake()):
        for _ in range(3):
            run()
        with pytest.raises(client.AILimitExceeded):
            run()
    assert AIRequestLog.objects.filter(status="rejected_by_limit").count() == 1
    assert AIRequestLog.objects.filter(status="ok").count() == 3


def test_tests_cannot_reach_real_api(ai_on):
    with pytest.raises(client.AIError):
        run()  # the autouse guard refuses; surfaced as a safe AIError


# ------------------------------------------------------------------ status page
def test_status_page_admin_only(login):
    assert login(Role.DIETICIAN).get("/ai/").status_code == 403


def test_status_page_never_shows_the_key(ai_on, login):
    html = login(Role.ADMIN).get("/ai/").content.decode()
    assert "налаштовано" in html and FAKE_KEY not in html and "sk-ant-test" not in html


def test_status_page_without_key_explains_where_to_put_it(login):
    html = login(Role.ADMIN).get("/ai/").content.decode()
    assert "не вказано" in html and "ANTHROPIC_API_KEY" in html and ".env" in html


def test_connection_check_button(ai_on, login, settings):
    settings.ANTHROPIC_MODEL_LIGHT = ""
    c = login(Role.ADMIN)
    with client.use_transport(fake({"ok": True})):
        html = c.post("/ai/", follow=True).content.decode()
    assert "З&#x27;єднання з Anthropic працює" in html or "З'єднання з Anthropic працює" in html
    assert AIRequestLog.objects.get().feature == "healthcheck"


def test_ai_check_command_prints_no_key(ai_on, capsys):
    call_command("ai_check")
    out = capsys.readouterr().out
    assert "є" in out and FAKE_KEY not in out


def test_ai_buttons_flag_in_templates(login, ai_on):
    r = login(Role.DIETICIAN).get("/")
    assert r.context["ai_available"] is True


# ------------------------------------------------------------------ AI-10 model per feature
@pytest.mark.parametrize("feature,model", [
    ("reference_duplicates", "claude-haiku-4-5"), ("menu_review", "claude-haiku-4-5"), ("dish_draft", "claude-haiku-4-5"),
    ("product_estimate", "claude-sonnet-5"), ("card_import", "claude-sonnet-5"), ("healthcheck", "claude-sonnet-5"),
])
def test_light_features_go_to_the_light_model(ai_on, settings, feature, model):
    settings.ANTHROPIC_MODEL, settings.ANTHROPIC_MODEL_LIGHT = "claude-sonnet-5", "claude-haiku-4-5"
    t = fake({"ok": True})
    with client.use_transport(t):
        client.extract(user=None, feature=feature, system_prompt="s", content_blocks=[{"type": "text", "text": "x"}],
                       tool_schema=client.PING_SCHEMA)
    assert t.calls[0]["model"] == model
    assert AIRequestLog.objects.get().model == model          # the log shows which model answered


def test_without_light_model_everything_uses_main(ai_on, settings):
    settings.ANTHROPIC_MODEL, settings.ANTHROPIC_MODEL_LIGHT = "claude-sonnet-5", ""
    assert client.model_for("menu_review") == "claude-sonnet-5"


def test_connection_check_pings_each_model_once(ai_on, settings):
    settings.ANTHROPIC_MODEL, settings.ANTHROPIC_MODEL_LIGHT = "claude-sonnet-5", "claude-haiku-4-5"
    t = fake({"ok": True})
    with client.use_transport(t):
        assert set(client.check_connection()) == {"claude-sonnet-5", "claude-haiku-4-5"}
    assert [c["model"] for c in t.calls] == ["claude-sonnet-5", "claude-haiku-4-5"]
    settings.ANTHROPIC_MODEL_LIGHT = "claude-sonnet-5"                # same model twice → one ping
    t = fake({"ok": True})
    with client.use_transport(t):
        client.check_connection()
    assert len(t.calls) == 1


def test_connection_check_names_the_failing_model(ai_on, settings):
    settings.ANTHROPIC_MODEL, settings.ANTHROPIC_MODEL_LIGHT = "claude-sonnet-5", "claude-haiku-4-5"

    def transport(**kw):
        if kw["model"] == "claude-haiku-4-5":
            raise client.AIError("Сервіс ШІ повернув помилку 404.")
        return client.Reply({"ok": True}, 5, 5)

    with client.use_transport(transport), pytest.raises(client.AIError, match="claude-haiku-4-5"):
        client.check_connection()


def test_status_page_shows_both_models(login, settings):
    settings.ANTHROPIC_MODEL, settings.ANTHROPIC_MODEL_LIGHT = "claude-sonnet-5", "claude-haiku-4-5"
    html = login(Role.ADMIN).get("/ai/").content.decode()
    assert "claude-sonnet-5" in html and "claude-haiku-4-5" in html and "Легка модель" in html
