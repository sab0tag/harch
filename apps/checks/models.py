"""Scheduled reference-data checks (docs/07_ai.md, AI-09). Findings come from code; the model may only
add a suggestion next to them, labelled «Запропоновано ШІ». Nothing here changes the catalog."""
from django.conf import settings
from django.db import models


class CheckRun(models.Model):
    class AIStatus(models.TextChoices):
        OFF = "off", "ШІ вимкнено"
        OK = "ok", "Пропозиції ШІ додано"
        ERROR = "error", "ШІ недоступний"

    run_date = models.DateField("Дата перевірки")
    created_at = models.DateTimeField(auto_now_add=True)
    task = models.OneToOneField("workflow.Task", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="check_run")
    ai_status = models.CharField(max_length=10, choices=AIStatus.choices, default=AIStatus.OFF)
    ai_summary = models.TextField("Резюме ШІ", blank=True)
    ai_error = models.CharField(max_length=300, blank=True)
    ai_log = models.ForeignKey("assist.AIRequestLog", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="+")

    class Meta:
        verbose_name = "Перевірка довідників"
        verbose_name_plural = "Перевірки довідників"
        ordering = ["-run_date", "-pk"]

    def __str__(self):
        return f"Перевірка довідників {self.run_date:%d.%m.%Y}"


class Finding(models.Model):
    class Severity(models.TextChoices):
        BAD = "bad", "Помилка"
        WARN = "warn", "Попередження"
        INFO = "info", "До відома"

    class Status(models.TextChoices):
        OPEN = "open", "Відкрито"
        FIXED = "fixed", "Виправлено"
        ACCEPTED = "accepted", "Норма, залишити"

    run = models.ForeignKey(CheckRun, related_name="findings", on_delete=models.CASCADE)
    check_code = models.CharField(max_length=40)
    severity = models.CharField(max_length=10, choices=Severity.choices)
    title = models.CharField(max_length=300)
    detail = models.TextField(blank=True)
    link = models.CharField(max_length=300, blank=True)       # screen where the fix is made
    ai_suggestion = models.TextField("Пропозиція ШІ", blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    comment = models.TextField(blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Знахідка"
        verbose_name_plural = "Знахідки"
        ordering = ["run", "pk"]

    def __str__(self):
        return self.title
