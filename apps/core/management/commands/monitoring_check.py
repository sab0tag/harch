"""Print the /healthz/full/ report and, with --sentry, send a test event (S5-05 setup check)."""
from django.core.management.base import BaseCommand, CommandError

from apps.core.health import full_report


class Command(BaseCommand):
    help = "Show monitoring status; --sentry sends a test message to Sentry."

    def add_arguments(self, parser):
        parser.add_argument("--sentry", action="store_true", help="Send a test message to Sentry")

    def handle(self, *args, sentry, **options):
        healthy, report = full_report()
        for key, value in report.items():
            self.stdout.write(f"{key:<10} {value}")
        if sentry:
            import sentry_sdk

            if not sentry_sdk.get_client().is_active():
                raise CommandError("Sentry is off: set SENTRY_DSN in .env and use prod settings.")
            event_id = sentry_sdk.capture_message("harch monitoring_check: test event")
            sentry_sdk.flush(timeout=10)
            self.stdout.write(self.style.SUCCESS(f"Sentry test event sent: {event_id}"))
        if not healthy:
            raise CommandError("degraded")
