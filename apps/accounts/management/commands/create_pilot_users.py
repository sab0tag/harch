import csv

from django.core.management.base import BaseCommand, CommandError

from apps.accounts import services


class Command(BaseCommand):
    help = ("S6-05: створити/оновити користувачів пілоту з таблиці (CSV або .xlsx). "
            "Зразок: docs/pilot_users.example.csv. Спершу --dry-run.")

    def add_arguments(self, parser):
        parser.add_argument("path", help="таблиця: логін (необов'язково), ПІБ, роль, відділення, телефон")
        parser.add_argument("--dry-run", action="store_true", help="лише перевірити і показати, нічого не записувати")
        parser.add_argument("--reset-passwords", action="store_true",
                            help="видати нові тимчасові паролі й тим, хто вже є")
        parser.add_argument("--deactivate-others", action="store_true",
                            help="вимкнути всіх активних, кого немає в таблиці (крім суперкористувачів)")
        parser.add_argument("--out", help="записати логіни й тимчасові паролі у CSV для роздачі (не в консоль)")

    def handle(self, path, dry_run, reset_passwords, deactivate_others, out, **opts):
        try:
            rows = services.read_table(path)
        except (OSError, ValueError, csv.Error) as e:
            raise CommandError(str(e)) from e
        result = services.import_users(rows, reset_passwords=reset_passwords,
                                       deactivate_others=deactivate_others, dry_run=dry_run)
        if result.errors:
            raise CommandError("Нічого не змінено. Виправте таблицю:\n  " + "\n  ".join(result.errors))
        people = result.created + result.updated
        if out:
            with open(out, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["Логін", "ПІБ", "Роль", "Відділення", "Тимчасовий пароль"])
                w.writerows([p["login"], p["name"], p["role"], p["department"], p["password"]] for p in people)
        for title, group in (("Створено", result.created), ("Оновлено", result.updated)):
            for p in group:
                secret = "" if out or not p["password"] else f"  пароль: {p['password']}"
                self.stdout.write(f"{title}: {p['login']:<18} {p['name']} · {p['role']}"
                                  f"{' · ' + p['department'] if p['department'] else ''}{secret}")
        for login in result.deactivated:
            self.stdout.write(f"Вимкнено: {login}")
        prefix = "ПЕРЕВІРКА, нічого не записано. " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"{prefix}Створено {len(result.created)}, оновлено {len(result.updated)}, вимкнено {len(result.deactivated)}."
            + (f" Паролі — у {out}." if out and not dry_run else "")))
