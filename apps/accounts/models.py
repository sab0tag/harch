from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.db.models import Q


class Role(models.TextChoices):
    ADMIN = "admin", "Адміністратор"
    DIETICIAN = "dietician", "Дієтсестра / лікар-дієтолог"
    CHEF = "chef", "Завідувач виробництва"
    DEPT_NURSE = "dept_nurse", "Старша медсестра відділення"
    ACCOUNTANT = "accountant", "Бухгалтер"


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

    def has_role(self, *roles) -> bool:
        """True if user has one of `roles`. Admin role passes every check."""
        return self.is_authenticated and (self.role == Role.ADMIN or self.role in roles)
