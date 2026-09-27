"""Health checks for monitoring (S5-05, docs/06_deploy.md «Мониторинг»).

`/healthz/` answers "is the app up" (DB only) — Docker and Caddy depend on it.
`/healthz/full/` is for the external uptime monitor: it also fails when the scheduler has
stopped beating or the last backup is too old, so one monitor catches silent failures.
"""
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone

from .models import Heartbeat

SCHEDULER = "scheduler"

OK, STALE, MISSING, ERROR, OFF = "ok", "stale", "missing", "error", "off"


def beat(name=SCHEDULER):
    Heartbeat.objects.update_or_create(name=name, defaults={"at": timezone.now()})


def check_db():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return OK
    except Exception:  # noqa: BLE001 — a health check must never raise
        return ERROR


def check_scheduler(now=None):
    now = now or timezone.now()
    try:
        last = Heartbeat.objects.filter(name=SCHEDULER).values_list("at", flat=True).first()
    except Exception:  # noqa: BLE001
        return ERROR
    if last is None:
        return MISSING
    return OK if now - last <= timedelta(minutes=settings.HEALTH_SCHEDULER_MAX_AGE_MINUTES) else STALE


def check_backup(now=None):
    """Age of the file backup.sh touches after a verified dump. Off when no path is configured (dev)."""
    path = settings.HEALTH_BACKUP_FILE
    if not path:
        return OFF
    try:
        mtime = Path(path).stat().st_mtime
    except FileNotFoundError:
        return MISSING
    except OSError:
        return ERROR
    now = now or timezone.now()
    age = now.timestamp() - mtime
    return OK if age <= settings.HEALTH_BACKUP_MAX_AGE_HOURS * 3600 else STALE


def full_report():
    report = {"db": check_db()}
    report["scheduler"] = check_scheduler() if report["db"] == OK else ERROR
    report["backup"] = check_backup()
    healthy = all(v in (OK, OFF) for v in report.values())
    return healthy, {"status": "ok" if healthy else "degraded", **report}
