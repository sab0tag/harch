from django.core.management.base import BaseCommand

from apps.catalog.audit import notify_fresh, run_audit


class Command(BaseCommand):
    help = "Нічна перевірка довідника (AI-09). З --ai ШІ додатково групує можливі дублі продуктів."

    def add_arguments(self, parser):
        parser.add_argument("--ai", action="store_true", help="використати ШІ для пошуку дублів (якщо увімкнено)")
        parser.add_argument("--quiet", action="store_true", help="не надсилати сповіщення")

    def handle(self, *args, ai=False, quiet=False, **opts):
        counts = run_audit(with_ai=ai)
        self.stdout.write(f"Нових: {counts['new']}, повернулось: {counts['reopened']}, "
                          f"виправлено: {counts['resolved']}, відкритих: {counts['open']}"
                          + (f", {counts['ai']}" if counts["ai"] and counts["ai"] != "ok" else ""))
        if not quiet:
            notify_fresh(counts)
