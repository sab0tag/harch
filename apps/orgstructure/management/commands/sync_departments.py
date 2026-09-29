from django.core.management.base import BaseCommand, CommandError

from apps.orgstructure import services


class Command(BaseCommand):
    help = ("Привести відділення до реального списку лікарні (seed/hospital/departments.json): порядок, "
            "основна дієта, категорія; решту вимкнути разом з їхніми медсестрами й відкритими замовленнями.")

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", default=str(services.HOSPITAL_DEPARTMENTS))
        parser.add_argument("--dry-run", action="store_true", help="лише показати")

    def handle(self, *args, path, dry_run=False, **opts):
        report = services.sync_departments(services.read_departments(path), dry_run=dry_run)
        if report.errors:
            raise CommandError("Нічого не змінено:\n  " + "\n  ".join(report.errors))
        for title, items in (("Додано", report.created), ("Оновлено", report.updated),
                             ("Вимкнено відділення", report.deactivated), ("Вимкнено медсестер", report.nurses_off),
                             ("Закрито замовлень", report.orders_closed)):
            for item in items:
                self.stdout.write(f"{title}: {item}")
        self.stdout.write(self.style.SUCCESS(
            f"{'ПЕРЕВІРКА, нічого не записано. ' if dry_run else ''}Додано {len(report.created)}, "
            f"оновлено {len(report.updated)}, вимкнено {len(report.deactivated)}."))
