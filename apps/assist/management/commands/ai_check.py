from django.core.management.base import BaseCommand, CommandError

from apps.assist import client


class Command(BaseCommand):
    help = "Показати стан ШІ і (з --ping) зробити один тестовий виклик. Ключ ніколи не друкується."

    def add_arguments(self, parser):
        parser.add_argument("--ping", action="store_true", help="зробити реальний мінімальний виклик API")

    def handle(self, *args, ping=False, **opts):
        st = client.status()
        self.stdout.write(f"AI_ENABLED:       {st['enabled']}")
        self.stdout.write(f"Ключ у .env:      {'є' if st['key_configured'] else 'немає'}")
        self.stdout.write(f"Модель:           {st['model']}")
        self.stdout.write(f"Сьогодні запитів: {st['used_today']} / {st['daily_limit']}")
        if not ping:
            return
        try:
            client.check_connection()
        except client.AIUnavailable as exc:
            raise CommandError(str(exc)) from exc
        except (client.AILimitExceeded, client.AIError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("З'єднання з Anthropic працює."))
