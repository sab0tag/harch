from django.conf import settings
from django.db import models

GRAMS = {"max_digits": 14, "decimal_places": 2}


class ConsumptionSnapshotLine(models.Model):
    """Розхід, зафіксований у момент затвердження плану (інваріант 5).

    Копіює назви й норми на той момент — подальші правки карток не змінюють історію.
    """

    plan = models.ForeignKey("planning.DailyPlan", related_name="snapshot", on_delete=models.PROTECT)
    diet_code = models.CharField(max_length=10)
    menu_number = models.PositiveIntegerField()
    meal_slot = models.CharField(max_length=20)
    dish_card_no = models.PositiveIntegerField()
    dish_name = models.CharField(max_length=200)
    dish_output_g = models.DecimalField(null=True, blank=True, **GRAMS)  # вихід порції на момент затвердження
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    product_name = models.CharField(max_length=150)
    portions = models.IntegerField()  # negative only on correction lines (discharge, transfer out)
    # S7-01: set on the lines an approved order correction added after the plan ("додаткова вимога")
    correction = models.ForeignKey("orders.OrderCorrection", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="snapshot_lines")
    brutto_per_portion_g = models.DecimalField(**GRAMS)
    netto_per_portion_g = models.DecimalField(**GRAMS)
    brutto_total_g = models.DecimalField(**GRAMS)
    netto_total_g = models.DecimalField(**GRAMS)
    kcal_total = models.DecimalField(**GRAMS)

    class Meta:
        verbose_name = "Рядок зафіксованого розходу"
        verbose_name_plural = "Зафіксований розхід"
        ordering = ["plan", "diet_code", "id"]

    def __str__(self):
        return f"{self.plan.service_date} {self.diet_code} {self.product_name} {self.brutto_total_g} г"


class ActualConsumption(models.Model):
    """Факт від шеф-кухаря: скільки продукту реально пішло (відхилення від плану, заміни)."""

    plan = models.ForeignKey("planning.DailyPlan", related_name="actuals", on_delete=models.PROTECT)
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, verbose_name="продукт")
    product_name = models.CharField(max_length=150)
    planned_brutto_g = models.DecimalField("план, г", **GRAMS)        # з снапшота; 0 для замін
    actual_brutto_g = models.DecimalField("факт, г", **GRAMS)
    reason = models.CharField("причина відхилення", max_length=200, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Фактичний розхід"
        verbose_name_plural = "Фактичний розхід"
        ordering = ["plan", "product_name"]
        constraints = [models.UniqueConstraint(fields=["plan", "product"], name="uniq_actual_plan_product")]

    @property
    def deviation_pct(self):
        if not self.planned_brutto_g:
            return None if not self.actual_brutto_g else 100
        return (self.actual_brutto_g - self.planned_brutto_g) / self.planned_brutto_g * 100

    def __str__(self):
        return f"{self.plan.service_date} {self.product_name}: {self.actual_brutto_g} г"


class WriteOff(models.Model):
    """Списання продуктів за день бухгалтерією. Рядки фіксуються при поданні (ціни на той момент)."""

    plan = models.OneToOneField("planning.DailyPlan", related_name="write_off", on_delete=models.PROTECT)
    task = models.OneToOneField("workflow.Task", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="write_off")
    total_cost = models.DecimalField("сума, грн", max_digits=14, decimal_places=2, null=True, blank=True)
    prepared_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Списання"
        verbose_name_plural = "Списання"

    def __str__(self):
        return f"Списання {self.plan.service_date:%d.%m.%Y}"


class WriteOffLine(models.Model):
    write_off = models.ForeignKey(WriteOff, related_name="lines", on_delete=models.CASCADE)
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    product_name = models.CharField(max_length=150)
    planned_brutto_g = models.DecimalField(**GRAMS)
    quantity_g = models.DecimalField("до списання, г", **GRAMS)
    price_per_kg = models.DecimalField("ціна/кг", max_digits=10, decimal_places=2)
    cost = models.DecimalField("сума", max_digits=14, decimal_places=2)
    reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["write_off", "product_name"]
        constraints = [models.UniqueConstraint(fields=["write_off", "product"], name="uniq_writeoff_product")]


class StoreIssue(models.Model):
    """«Видано»: the store handed the day's requisition (correction=None) or one «додаткова вимога» to the kitchen.
    A mark with who and when, not a document: quantities are the plan's; a difference goes in the note."""

    plan = models.ForeignKey("planning.DailyPlan", on_delete=models.CASCADE, related_name="store_issues")
    correction = models.ForeignKey("orders.OrderCorrection", null=True, blank=True, on_delete=models.CASCADE,
                                   related_name="store_issues")
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name="Видав")
    issued_at = models.DateTimeField("Видано", auto_now_add=True)
    note = models.CharField("Примітка", max_length=300, blank=True)

    class Meta:
        verbose_name = "Видача зі складу"
        verbose_name_plural = "Видачі зі складу"
        constraints = [
            models.UniqueConstraint(fields=["plan"], condition=models.Q(correction__isnull=True), name="store_issue_one_main"),
            models.UniqueConstraint(fields=["plan", "correction"], name="store_issue_one_per_correction"),
        ]

    def __str__(self):
        return f"Видано {self.issued_at:%d.%m %H:%M}"
