"""Notify about overdue tasks (once per task). Scheduled every 15 minutes."""
from django.core.management.base import BaseCommand

from apps.workflow.services import notify_overdue


class Command(BaseCommand):
    help = "Send overdue notifications for open tasks past their deadline."

    def handle(self, *args, **options):
        tasks = notify_overdue()
        self.stdout.write(f"overdue notified: {len(tasks)}")
