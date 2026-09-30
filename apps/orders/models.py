from django.conf import settings
from django.db import models

from apps.catalog.models import MealSlot


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


class OrderCorrection(models.Model):
    """S7-01: «добавка до вимоги» after the order is approved — admissions, discharges, transfers, diet changes.

    Lines hold ± portions per department and diet from `from_meal` on; a transfer carries both sides
    (+ for this department, − for `transfer_from`). Approved corrections become extra lines of the day's
    frozen consumption (ConsumptionSnapshotLine.correction), never changes to the approved order itself.
    """

    class Reason(models.TextChoices):
        ADMISSION = "admission", "Надходження"
        DISCHARGE = "discharge", "Виписка"
        TRANSFER = "transfer", "Переведення з іншого відділення"
        DIET_CHANGE = "diet_change", "Зміна дієти / алергія"
        LATE_ORDER = "late_order", "Пізнє замовлення"   # system-made: see corrections.take_late_order

    department = models.ForeignKey("orgstructure.Department", verbose_name="Відділення", on_delete=models.PROTECT,
                                   related_name="corrections")
    service_date = models.DateField("Дата обслуговування")
    task = models.OneToOneField("workflow.Task", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="order_correction")
    from_meal = models.CharField("З прийому їжі", max_length=20, choices=MealSlot.choices)
    reason = models.CharField("Причина", max_length=20, choices=Reason.choices, default=Reason.ADMISSION)
    transfer_from = models.ForeignKey("orgstructure.Department", verbose_name="З відділення", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="transfers_out")
    note = models.TextField("Примітка", blank=True)
    applied_at = models.DateTimeField("Додано до розходу", null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Коригування замовлення"
        verbose_name_plural = "Коригування замовлень"
        ordering = ["service_date", "created_at"]

    def __str__(self):
        return f"Коригування · {self.department} · {self.service_date:%d.%m.%Y}"

    FROM_MEAL_PHRASES = {"breakfast": "зі сніданку", "second_breakfast": "з 2-го сніданку", "lunch": "з обіду",
                         "afternoon_snack": "з полуденку", "dinner": "з вечері"}

    @property
    def from_meal_phrase(self):
        return self.FROM_MEAL_PHRASES.get(self.from_meal, self.from_meal)


class OrderCorrectionLine(models.Model):
    correction = models.ForeignKey(OrderCorrection, related_name="lines", on_delete=models.CASCADE)
    department = models.ForeignKey("orgstructure.Department", verbose_name="Відділення", on_delete=models.PROTECT)
    diet = models.ForeignKey("catalog.Diet", verbose_name="Дієта", on_delete=models.PROTECT)
    delta = models.IntegerField("± порцій")

    class Meta:
        verbose_name = "Рядок коригування"
        verbose_name_plural = "Рядки коригування"
        ordering = ["department__sort_order", "diet__sort_order"]

    def __str__(self):
        return f"{self.department} · {self.diet.code}: {self.delta:+d}"
