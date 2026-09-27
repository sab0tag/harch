"""S5-05: monitoring — /healthz/full/, scheduler heartbeat, backup age, Sentry setup."""
import os
import time
from datetime import timedelta
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.core import health
from apps.core.models import Heartbeat
from config.sentry import init_sentry

pytestmark = pytest.mark.django_db


@pytest.fixture
def backup_file(tmp_path, settings):
    path = tmp_path / ".last_success"
    path.write_text("2026-09-27 02:30:00")
    settings.HEALTH_BACKUP_FILE = str(path)
    return path


def age_file(path, hours):
    t = time.time() - hours * 3600
    os.utime(path, (t, t))


def test_full_ok_when_scheduler_beats_and_backup_fresh(client, backup_file):
    health.beat()
    response = client.get("/healthz/full/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "scheduler": "ok", "backup": "ok"}


def test_backup_not_monitored_when_no_file_configured(client, settings):
    settings.HEALTH_BACKUP_FILE = ""
    health.beat()
    response = client.get("/healthz/full/")
    assert response.status_code == 200
    assert response.json()["backup"] == "off"


def test_scheduler_never_ran_is_degraded(client, backup_file):
    response = client.get("/healthz/full/")
    assert response.status_code == 503
    assert response.json()["scheduler"] == "missing"


def test_scheduler_stale_after_threshold(client, backup_file, settings):
    settings.HEALTH_SCHEDULER_MAX_AGE_MINUTES = 45
    Heartbeat.objects.create(name=health.SCHEDULER, at=timezone.now() - timedelta(minutes=46))
    response = client.get("/healthz/full/")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": "ok", "scheduler": "stale", "backup": "ok"}


def test_backup_stale_and_missing(client, backup_file, settings):
    health.beat()
    settings.HEALTH_BACKUP_MAX_AGE_HOURS = 26
    age_file(backup_file, 27)
    assert client.get("/healthz/full/").json()["backup"] == "stale"
    backup_file.unlink()
    response = client.get("/healthz/full/")
    assert response.status_code == 503
    assert response.json()["backup"] == "missing"


def test_liveness_ignores_scheduler_and_backup(client, backup_file):
    """Docker and Caddy depend on /healthz/; a stale backup must not take the site down."""
    backup_file.unlink()
    response = client.get("/healthz/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


def test_check_deadlines_is_the_scheduler_heartbeat():
    before = timezone.now()
    call_command("check_deadlines", stdout=open(os.devnull, "w"))
    assert Heartbeat.objects.get(name=health.SCHEDULER).at >= before
    call_command("check_deadlines", stdout=open(os.devnull, "w"))
    assert Heartbeat.objects.count() == 1


def test_full_is_public_and_reveals_only_states(client, backup_file):
    """The uptime monitor has no login; the answer holds status words only — no paths, times or errors."""
    health.beat()
    response = client.get("/healthz/full/")
    assert response.status_code == 200
    assert set(response.json().values()) <= {"ok", "off", "stale", "missing", "error", "degraded"}
    assert str(backup_file) not in response.content.decode()


def test_monitoring_check_command(backup_file, capsys):
    with pytest.raises(CommandError, match="degraded"):
        call_command("monitoring_check")
    health.beat()
    call_command("monitoring_check")
    assert "scheduler  ok" in capsys.readouterr().out


def test_sentry_off_without_dsn():
    with mock.patch("sentry_sdk.init") as init:
        assert init_sentry("", "production") is False
    init.assert_not_called()


def test_sentry_sends_no_personal_data():
    with mock.patch("sentry_sdk.init") as init:
        assert init_sentry("https://key@o0.ingest.sentry.io/0", "production", "abc123") is True
    kwargs = init.call_args.kwargs
    assert kwargs["send_default_pii"] is False
    assert kwargs["max_request_body_size"] == "never"
    assert kwargs["environment"] == "production"
    assert kwargs["release"] == "abc123"
