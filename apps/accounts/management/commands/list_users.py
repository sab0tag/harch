from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import User


class Command(BaseCommand):
    help = "Користувачі: логін, ПІБ, роль, відділення, стан, останній вхід (ops/harch users)."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="разом з вимкненими")

    def handle(self, *args, all=False, **opts):
        users = User.objects.select_related("department").order_by("role", "last_name", "username")
        if not all:
            users = users.filter(is_active=True)
        for u in users:
            flags = [f for f, on in (("ВИМКНЕНО", not u.is_active), ("тимчасовий пароль", u.must_change_password),
                                     ("суперкористувач", u.is_superuser)) if on]
            last = timezone.localtime(u.last_login).strftime("%d.%m %H:%M") if u.last_login else "ще не входив(ла)"
            self.stdout.write(f"{u.username:<20} {(u.get_full_name() or '—'):<32} {u.get_role_display():<28} "
                              f"{(u.department.name if u.department else ''):<20} {last:<17} {', '.join(flags)}")
        self.stdout.write(f"Разом: {users.count()}")
