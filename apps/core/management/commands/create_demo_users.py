"""One user per role so the UI can be clicked through. Dev and training copy only (DEBUG or DEMO_MODE)."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Role, User
from apps.orgstructure.models import Department

PASSWORD = "demo12345"
DEMO = [
    ("admin", "Адмін", "Системний", Role.ADMIN),
    ("dietician", "Олена", "Дієтсестра", Role.DIETICIAN),
    ("chef", "Галина", "Шеф", Role.CHEF),
    ("nurse", "Марія", "Медсестра", Role.DEPT_NURSE),
    ("accountant", "Ірина", "Бухгалтер", Role.ACCOUNTANT),
]


class Command(BaseCommand):
    help = f"Create demo users (one per role), password '{PASSWORD}'. Dev/training copy only."

    def handle(self, *args, **options):
        if not (settings.DEBUG or settings.DEMO_MODE):
            raise CommandError("create_demo_users runs only in dev (DEBUG) or on the training copy (DEMO_MODE)")
        dept, _ = Department.objects.get_or_create(name="Терапевтичне")
        for username, first, last, role in DEMO:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"first_name": first, "last_name": last, "role": role,
                          "department": dept if role == Role.DEPT_NURSE else None,
                          "is_staff": role == Role.ADMIN, "is_superuser": role == Role.ADMIN},
            )
            if created:
                user.set_password(PASSWORD)
                user.save()
            self.stdout.write(f"{'created' if created else 'exists '}  {username:<11} {user.get_role_display()}")
