"""Scheduled reference-data checks (deploy/crontab, weekly). Idempotent per day.

    python manage.py run_checks            # today's run, skipped if it already exists
    python manage.py run_checks --force    # another run today (e.g. after fixes)
    python manage.py run_checks --no-ai    # code checks only
"""
from django.core.management.base import BaseCommand, CommandError

from apps.checks.services import AlreadyReviewed, run_checks


class Command(BaseCommand):
    help = "Check reference data (menus vs norms, products, cards); open a review task if anything is found."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--no-ai", action="store_true")

    def handle(self, *args, force=False, no_ai=False, **options):
        try:
            run, created = run_checks(force=force, use_ai=not no_ai)
        except AlreadyReviewed as exc:
            raise CommandError(str(exc)) from exc
        if not created:
            self.stdout.write(f"{run.run_date}: already checked today (use --force)")
            return
        n = run.findings.count()
        self.stdout.write(f"{run.run_date}: {n} findings, AI: {run.get_ai_status_display()}"
                          + (f" — {run.ai_error}" if run.ai_error else "")
                          + (f", task #{run.task_id}" if run.task_id else ""))
