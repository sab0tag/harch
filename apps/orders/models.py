from django.conf import settings
from django.db import models


class DailyOrder(models.Model):
    """Порційна вимога відділення на дату обслуговування (без даних пацієнтів — лише порції)."""

    department = models.ForeignKey("orgstructure.Department", verbose_name="Відділення", on_delete=models.PROTECT)
    service_date = models.DateField("Дата обслуговування")
    task = models.OneToOneField(
        "workflow.Task", null=True, blank=True, on_delete=models.SET_NULL, related_name="daily_order"
    )
    no_patients = models.BooleanField("Пацієнтів немає", default=False)
    note = models.TextField("Примітка", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = "Замовлення відділення"
        verbose_name_plural = "Замовлення відділень"
        ordering = ["service_date", "department__sort_order", "department__name"]
        constraints = [
            models.UniqueConstraint(fields=["department", "service_date"], name="order_unique_department_date"),
        ]

    def __str__(self):
        return f"{self.department} · {self.service_date:%d.%m.%Y}"

    @property
    def total_portions(self):
        return sum(line.portions for line in self.lines.all())


class DailyOrderLine(models.Model):
    order = models.ForeignKey(DailyOrder, related_name="lines", on_delete=models.CASCADE)
    diet = models.ForeignKey("catalog.Diet", verbose_name="Дієта", on_delete=models.PROTECT)
    portions = models.PositiveIntegerField("Порцій")

    class Meta:
        verbose_name = "Рядок замовлення"
        verbose_name_plural = "Рядки замовлення"
        ordering = ["diet__sort_order", "diet__code"]
        constraints = [models.UniqueConstraint(fields=["order", "diet"], name="order_line_unique_diet")]

    def __str__(self):
        return f"{self.diet.code}: {self.portions}"
