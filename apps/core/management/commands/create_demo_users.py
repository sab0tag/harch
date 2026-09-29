"""One user per role so the UI can be clicked through, plus a head nurse for every active department
(nurse-surgery, nurse-cardio, … — the same logins as the pilot placeholders). Dev and training copy only."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Role, User
from apps.accounts.services import translit
from apps.orgstructure.models import Department

PASSWORD = "demo12345"
DEMO = [
    ("admin", "Адмін", "Системний", Role.ADMIN),
    ("dietician", "Олена", "Дієтсестра", Role.DIETICIAN),
    ("chef", "Галина", "Шеф", Role.CHEF),
    ("nurse", "Марія", "Медсестра", Role.DEPT_NURSE),
    ("accountant", "Ірина", "Бухгалтер", Role.ACCOUNTANT),
]
# Department → login suffix, as on the pilot server; other departments get a transliterated name.
NURSE_LOGINS = {
    "Терапевтичне": "therapy", "Хірургічне": "surgery", "Кардіологічне": "cardio", "Неврологічне": "neuro",
    "Педіатричне": "pediatrics", "Гінекологічне": "gyneco", "Травматологічне": "trauma", "Урологічне": "uro",
    "Гастроентерологічне": "gastro", "Пульмонологічне": "pulmo", "Ендокринологічне": "endo",
    "Нефрологічне": "nephro", "Опікове": "burns", "Реанімація": "icu", "Пологове": "maternity",
}


def nurse_login(department):
    return "nurse-" + (NURSE_LOGINS.get(department.name) or translit(department.name))


class Command(BaseCommand):
    help = f"Create demo users (one per role + a nurse per department), password '{PASSWORD}'. Dev/training copy only."

    def handle(self, *args, **options):
        if not (settings.DEBUG or settings.DEMO_MODE):
            raise CommandError("create_demo_users runs only in dev (DEBUG) or on the training copy (DEMO_MODE)")
        dept, _ = Department.objects.get_or_create(name="Терапевтичне")
        people = [(username, {"first_name": first, "last_name": last, "role": role,
                              "department": dept if role == Role.DEPT_NURSE else None,
                              "is_staff": role == Role.ADMIN, "is_superuser": role == Role.ADMIN})
                  for username, first, last, role in DEMO]
        people += [(nurse_login(d), {"first_name": "Старша медсестра", "last_name": d.name,
                                     "role": Role.DEPT_NURSE, "department": d})
                   for d in Department.objects.filter(is_active=True)]
        for username, defaults in people:
            user, created = User.objects.get_or_create(username=username, defaults=defaults)
            if created:
                user.set_password(PASSWORD)
                user.save()
            self.stdout.write(f"{'created' if created else 'exists '}  {username:<17} {user.get_role_display()}"
                              f"{' · ' + user.department.name if user.department else ''}")
