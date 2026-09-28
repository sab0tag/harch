"""Who is told what, and when (docs/03_task_engine.md, «Уведомления»).

v0.1: in-app notifications only; email is sent additionally when NOTIFY_EMAIL=True and the
user has an address. Admins are not spammed with role notifications — they see everything
in the task lists anyway.
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.utils import timezone

from apps.accounts.models import Role

from .models import Notification

log = logging.getLogger(__name__)


def users_with_role(role, department=None):
    qs = get_user_model().objects.filter(is_active=True, role=role)
    if role == Role.DEPT_NURSE and department is not None:
        qs = qs.filter(department=department)
    return qs


def makers(task):
    if task.maker_id:
        return get_user_model().objects.filter(pk=task.maker_id, is_active=True)
    return users_with_role(task.maker_role, task.department)


def checkers(task):
    if task.checker_id:
        return get_user_model().objects.filter(pk=task.checker_id, is_active=True)
    return users_with_role(task.checker_role, task.department)


def notify(users, task, title, body="", url=""):
    users = [u for u in users if u is not None]
    if not users:
        return []
    now = timezone.now()
    created = Notification.objects.bulk_create(
        [Notification(user=u, task=task, title=title, body=body, url=url, sent_at=now) for u in users]
    )
    if getattr(settings, "NOTIFY_EMAIL", False):
        for user in users:
            if user.email:
                try:
                    send_mail(title, body or title, None, [user.email], fail_silently=False)
                except Exception:  # noqa: BLE001 — email must never break a transition
                    log.exception("Email notification failed for user %s", user.pk)
    return created
