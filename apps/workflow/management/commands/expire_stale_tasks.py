"""Close department orders left open after their day has passed (nightly, deploy/crontab).

    python manage.py expire_stale_tasks --dry-run   # show what would be closed
"""
from django.core.management.base import BaseCommand

from apps.workflow.services import expire_stale_tasks


class Command(BaseCommand):
    help = "Закрити незаповнені замовлення за дати, що минули (без сповіщень)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="лише показати")

    def handle(self, *args, dry_run=False, **opts):
        tasks = expire_stale_tasks(dry_run=dry_run)
        for t in tasks:
            self.stdout.write(f"{'буде закрито' if dry_run else 'закрито'}: {t}")
        self.stdout.write(f"{'Перевірка: ' if dry_run else ''}{len(tasks)} задач(і).")
