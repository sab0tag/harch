"""Notify about overdue tasks (once per task). Scheduled every 15 minutes.

Also the scheduler's heartbeat for /healthz/full/ (S5-05): it is the most frequent cron job.
"""
from django.core.management.base import BaseCommand

from apps.core.health import beat
from apps.workflow.services import notify_overdue


class Command(BaseCommand):
    help = "Send overdue notifications for open tasks past their deadline."

    def handle(self, *args, **options):
        tasks = notify_overdue()
        beat()
        self.stdout.write(f"overdue notified: {len(tasks)}")
