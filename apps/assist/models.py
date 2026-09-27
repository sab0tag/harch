from django.conf import settings
from django.db import models


class AIRequestLog(models.Model):
    """One row per model call. Stores metadata and the structured answer, never the key or uploads."""

    class Status(models.TextChoices):
        OK = "ok", "Успішно"
        ERROR = "error", "Помилка"
        REJECTED_BY_LIMIT = "rejected_by_limit", "Відхилено: денний ліміт"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="ai_requests", verbose_name="користувач")
    feature = models.CharField("функція", max_length=50)
    model = models.CharField("модель", max_length=100)
    status = models.CharField("статус", max_length=20, choices=Status.choices)
    input_tokens = models.PositiveIntegerField("вхідні токени", null=True, blank=True)
    output_tokens = models.PositiveIntegerField("вихідні токени", null=True, blank=True)
    duration_ms = models.PositiveIntegerField("тривалість, мс", null=True, blank=True)
    request_meta = models.JSONField("параметри запиту", default=dict, blank=True)
    response_json = models.JSONField("відповідь", null=True, blank=True)
    error = models.TextField("помилка", blank=True)
    created_at = models.DateTimeField("час", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "виклик ШІ"
        verbose_name_plural = "журнал ШІ"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.feature} · {self.get_status_display()} · {self.created_at:%d.%m %H:%M}"
