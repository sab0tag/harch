from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone


class TaskStatus(models.TextChoices):
    PENDING = "pending", "Очікує"
    IN_PROGRESS = "in_progress", "В роботі"
    SUBMITTED = "submitted", "На перевірці"
    APPROVED = "approved", "Затверджено"
    REJECTED = "rejected", "Повернуто на доопрацювання"
    CANCELLED = "cancelled", "Скасовано"


OPEN_FOR_MAKER = (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.REJECTED)
CLOSED = (TaskStatus.APPROVED, TaskStatus.CANCELLED)
# Types that may have several tasks per date and department (definitions.py registers them repeatable).
REPEATABLE_TYPES = ("order_correction",)


class TaskQuerySet(models.QuerySet):
    def open_for_maker(self):
        return self.filter(status__in=OPEN_FOR_MAKER)

    def overdue(self, now=None):
        from .registry import all_definitions

        alerting = [d.type for d in all_definitions() if d.overdue_alerts]
        return self.open_for_maker().filter(type__in=alerting, deadline__lt=now or timezone.now())


class Task(models.Model):
    """One handoff between roles (docs/03_task_engine.md). Transitions only via services."""

    type = models.CharField("Тип", max_length=40)
    status = models.CharField("Статус", max_length=20, choices=TaskStatus.choices, default=TaskStatus.PENDING)
    service_date = models.DateField("Дата обслуговування")
    department = models.ForeignKey(
        "orgstructure.Department", verbose_name="Відділення", null=True, blank=True, on_delete=models.PROTECT
    )
    maker_role = models.CharField(max_length=20)
    maker = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )  # null = any user with maker_role
    checker_role = models.CharField(max_length=20)
    checker = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    subject = GenericForeignKey("content_type", "object_id")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")
    deadline = models.DateTimeField("Дедлайн", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reject_count = models.PositiveIntegerField(default=0)
    overdue_notified_at = models.DateTimeField(null=True, blank=True)

    objects = TaskQuerySet.as_manager()

    class Meta:
        verbose_name = "Задача"
        verbose_name_plural = "Задачі"
        ordering = ["service_date", "deadline", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["type", "service_date", "department"],
                name="task_unique_per_type_date_department",
                nulls_distinct=False,
                condition=~models.Q(type__in=REPEATABLE_TYPES),  # several corrections a day are normal
            ),
        ]
        indexes = [models.Index(fields=["status", "deadline"]), models.Index(fields=["service_date", "type"])]

    def __str__(self):
        dept = f" · {self.department}" if self.department_id else ""
        return f"{self.title} на {self.service_date:%d.%m.%Y}{dept}"

    @property
    def definition(self):
        from .registry import get

        return get(self.type)

    @property
    def title(self):
        return self.definition.title

    def is_overdue(self, now=None):
        return (bool(self.deadline) and self.definition.overdue_alerts and self.status in OPEN_FOR_MAKER
                and self.deadline < (now or timezone.now()))

    @property
    def last_rejection(self):
        return self.events.filter(kind=TaskEvent.Kind.REJECTED).order_by("-created_at").first()


class TaskEvent(models.Model):
    """Immutable journal of everything that happened to a task."""

    class Kind(models.TextChoices):
        CREATED = "created", "Створено"
        STARTED = "started", "Розпочато"
        SUBMITTED = "submitted", "Подано на перевірку"
        APPROVED = "approved", "Затверджено"
        REJECTED = "rejected", "Повернуто"
        CANCELLED = "cancelled", "Скасовано"
        OVERDUE = "overdue", "Прострочено"
        REOPENED = "reopened", "Відкрито заново"
        COMMENT = "comment", "Коментар"

    task = models.ForeignKey(Task, related_name="events", on_delete=models.CASCADE)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Подія задачі"
        verbose_name_plural = "Журнал подій"
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.task}"


class Notification(models.Model):
    class Channel(models.TextChoices):
        IN_APP = "in_app", "У системі"
        EMAIL = "email", "Email"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="notifications", on_delete=models.CASCADE)
    task = models.ForeignKey(Task, null=True, blank=True, related_name="notifications", on_delete=models.CASCADE)
    channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.IN_APP)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    url = models.CharField(max_length=300, blank=True)   # where to go for notifications without a task
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Сповіщення"
        verbose_name_plural = "Сповіщення"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "read_at"])]

    def __str__(self):
        return self.title
