"""S6-06: the training copy back to a fresh, lived-in day (nightly, deploy/crontab.demo).

Wipes the WHOLE database, then: seed reference data → demo users → N past days played through
the chain → today up to the kitchen, tomorrow's orders waiting for review. Runs only with
DEMO_MODE and on a database whose name contains "demo" — never on the real system.
"""
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


def database_name():
    return connection.settings_dict["NAME"]


class Command(BaseCommand):
    help = "Training copy only: wipe the database and rebuild a lived-in demo day."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7, help="Past days of history (1–31)")

    def handle(self, *args, days=7, **options):
        if not settings.DEMO_MODE or "demo" not in database_name():
            raise CommandError("reset_demo wipes the database: only with DEMO_MODE=true on a *demo* database "
                               f"(this one: {database_name()!r}).")
        quiet = open("/dev/null", "w")
        call_command("flush", interactive=False, verbosity=0)
        call_command("load_seed", str(Path(settings.BASE_DIR) / "seed"), verbosity=0, stdout=quiet)
        call_command("demo_days", days=days, live=True, stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS("training copy reset"))
