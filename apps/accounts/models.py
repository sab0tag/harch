from django.contrib.auth.models import AbstractUser, UserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class Role(models.TextChoices):
    ADMIN = "admin", "Адміністратор"
    DIETICIAN = "dietician", "Дієтсестра / лікар-дієтолог"
    CHEF = "chef", "Завідувач виробництва"
    DEPT_NURSE = "dept_nurse", "Старша медсестра відділення"
    ACCOUNTANT = "accountant", "Бухгалтер"
    STOREKEEPER = "storekeeper", "Завідувач складу"
    DIRECTOR = "director", "Директор"


class HarchUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    role = models.CharField("Роль", max_length=20, choices=Role.choices)
    department = models.ForeignKey(
        "orgstructure.Department",
        verbose_name="Відділення",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="users",
    )
    phone = models.CharField("Телефон", max_length=20, blank=True)
    telegram_chat_id = models.CharField("Telegram chat id", max_length=32, blank=True)
    # S7-06: a second login of the same person for substitution (the chef covering the dietician) points
    # to the person's main login; approvals across the two are allowed but marked in the history.
    main_login = models.ForeignKey(
        "self", verbose_name="Основний вхід (та сама людина)", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="extra_logins",
        help_text="Для другого входу заміщення: шеф-кухар, що заміщує дієтсестру, має окремий вхід з роллю дієтсестри.",
    )
    must_change_password = models.BooleanField(
        "Змінити пароль при вході", default=False,
        help_text="Тимчасовий пароль: після входу система попросить задати власний (S6-05).",
    )

    objects = HarchUserManager()

    class Meta:
        verbose_name = "Користувач"
        verbose_name_plural = "Користувачі"
        constraints = [
            models.CheckConstraint(
                condition=Q(role__in=Role.values),
                name="user_role_valid",
            ),
            models.CheckConstraint(
                condition=~Q(role=Role.DEPT_NURSE) | Q(department__isnull=False),
                name="dept_nurse_requires_department",
            ),
        ]

    def __str__(self):
        return self.get_full_name() or self.username

    def is_same_person(self, other) -> bool:
        """Two logins of one person (main + substitution login), or the same login."""
        if other is None:
            return False
        return self.person_root_id() == other.person_root_id()

    def person_root_id(self):
        """Pk of the end of the `main_login` chain (any depth); a loop entered by mistake stops the walk."""
        path, user = [self.pk], self
        while user.main_login_id:
            if user.main_login_id in path:     # a loop: the same answer from every login on or leading into it
                return min(path[path.index(user.main_login_id):])
            path.append(user.main_login_id)
            user = user.main_login
        return user.pk

    def clean(self):
        super().clean()
        from .services import main_login_error

        error = main_login_error(self.username, self.main_login)
        if error:
            raise ValidationError({"main_login": error})

    def has_role(self, *roles) -> bool:
        """True if user has one of `roles`. Admin role passes every check."""
        return self.is_authenticated and (self.role == Role.ADMIN or self.role in roles)
