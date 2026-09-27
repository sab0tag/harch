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
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    product_name = models.CharField(max_length=150)
    portions = models.PositiveIntegerField()
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
