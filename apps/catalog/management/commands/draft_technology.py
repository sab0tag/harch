"""AI-10: propose «технологія» for all cards without it, in batches — drafts only, nothing is written.

    python manage.py draft_technology --limit 130

People accept the proposals on «Картки-розкладки → Технології з ШІ».
"""
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.assist import client
from apps.catalog import technology


class Command(BaseCommand):
    help = "Запропонувати технологію приготування (ШІ) для карток без неї. Лише чернетки."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=technology.BATCH * 3, help="скільки карток (за замовчуванням 30)")

    def handle(self, *args, limit=30, **opts):
        if not client.is_available():
            raise CommandError("ШІ вимкнено або ключ не налаштовано.")
        made = 0
        while made < limit:
            try:
                draft = technology.generate(None, min(technology.BATCH, limit - made))
            except (client.AIError, client.AILimitExceeded) as exc:
                raise CommandError(f"Зупинено після {made} карток: {exc}") from exc
            except ValidationError as exc:
                raise CommandError(f"Зупинено після {made} карток: {exc.messages[0]}") from exc
            if draft is None:
                break
            made += len(draft.input["dish_ids"])
            self.stdout.write(f"Пропозиція #{draft.pk}: {len(draft.result['items'])} з {len(draft.input['dish_ids'])} карток")
        self.stdout.write(f"Готово: запропоновано для {made} карток. Рішення — на сторінці «Технології з ШІ».")
