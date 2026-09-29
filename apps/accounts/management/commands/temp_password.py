from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.accounts.services import temporary_password


class Command(BaseCommand):
    help = ("Забутий пароль: видати тимчасовий, увімкнути обліковий запис і зняти блокування входу. "
            "При вході система попросить задати власний пароль (ops/harch temp-password).")

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, username, **opts):
        user = User.objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"немає користувача «{username}» (список: manage.py list_users --all)")
        password = temporary_password()
        user.set_password(password)
        user.must_change_password = True
        user.is_active = True
        user.save()
        call_command("axes_reset_username", username, stdout=open("/dev/null", "w"))
        self.stdout.write(f"{username} · {user.get_full_name()} — тимчасовий пароль: {password}")
