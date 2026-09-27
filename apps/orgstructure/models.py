from django.db import models
from simple_history.models import HistoricalRecords


class Department(models.Model):
    """Відділення лікарні."""

    class PatientCategory(models.TextChoices):
        ADULTS = "adults", "Дорослі"
        CHILDREN = "children", "Діти"
        MATERNITY = "maternity", "Породіллі"

    name = models.CharField("Назва", max_length=120, unique=True)
    patient_category = models.CharField(
        "Категорія пацієнтів",
        max_length=20,
        choices=PatientCategory.choices,
        default=PatientCategory.ADULTS,
    )
    default_diet = models.ForeignKey(
        "catalog.Diet",
        verbose_name="Дієта за замовчуванням",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="departments",
    )
    beds = models.PositiveIntegerField("Ліжок", null=True, blank=True)
    sort_order = models.PositiveIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Активне", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    class Meta:
        verbose_name = "Відділення"
        verbose_name_plural = "Відділення"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name
