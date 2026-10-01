from django.core.exceptions import ValidationError
from django.db import models


class DailyPlan(models.Model):
    """Меню-розкладка на день: одне меню на кожну дієту з порціями."""

    service_date = models.DateField("Дата обслуговування", unique=True)
    task = models.OneToOneField(
        "workflow.Task", null=True, blank=True, on_delete=models.SET_NULL, related_name="daily_plan"
    )
    approved_at = models.DateTimeField("Затверджено", null=True, blank=True)
    # The director's «Затверджую» on the printed menu-rozkladka, given in the system (no name is printed).
    signed_at = models.DateTimeField("Підписано директором", null=True, blank=True)
    signed_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="signed_plans", verbose_name="Підписав")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "План меню на день"
        verbose_name_plural = "Плани меню"
        ordering = ["-service_date"]

    def __str__(self):
        return f"План меню на {self.service_date:%d.%m.%Y}"

    @property
    def is_frozen(self):
        return self.approved_at is not None


class DailyPlanEntry(models.Model):
    plan = models.ForeignKey(DailyPlan, related_name="entries", on_delete=models.CASCADE)
    diet = models.ForeignKey("catalog.Diet", verbose_name="Дієта", on_delete=models.PROTECT)
    menu = models.ForeignKey("menus.Menu", verbose_name="Меню", null=True, blank=True, on_delete=models.PROTECT)
    portions = models.PositiveIntegerField("Порцій")

    class Meta:
        verbose_name = "Дієта в плані"
        verbose_name_plural = "Дієти в плані"
        ordering = ["diet__sort_order", "diet__code"]
        constraints = [models.UniqueConstraint(fields=["plan", "diet"], name="plan_entry_unique_diet")]

    def __str__(self):
        return f"{self.diet.code}: меню №{self.menu.number if self.menu else '—'} × {self.portions}"

    def clean(self):
        if self.menu_id and self.diet_id and self.menu.diet_id != self.diet_id:
            raise ValidationError({"menu": f"Меню №{self.menu.number} належить іншій дієті."})
