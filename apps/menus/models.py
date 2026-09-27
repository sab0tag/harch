from django.core.exceptions import ValidationError
from django.db import models
from simple_history.models import HistoricalRecords

from apps.catalog.models import Diet, Dish, MealSlot


class Menu(models.Model):
    """Меню на день для однієї дієти (інваріант: рівно одна дієта)."""

    number = models.PositiveIntegerField("№ меню", unique=True)
    diet = models.ForeignKey(Diet, verbose_name="Дієта", on_delete=models.PROTECT, related_name="menus")
    title = models.CharField("Назва", max_length=200, blank=True)
    is_active = models.BooleanField("Активне", default=True)
    verified = models.BooleanField("Перевірено", default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    class Meta:
        verbose_name = "Меню"
        verbose_name_plural = "Меню"
        ordering = ["number"]

    def __str__(self):
        return f"Меню №{self.number} ({self.diet.code})"


class MenuItem(models.Model):
    menu = models.ForeignKey(Menu, related_name="items", on_delete=models.CASCADE)
    meal_slot = models.CharField("Прийом їжі", max_length=20, choices=MealSlot.choices)
    dish = models.ForeignKey(Dish, verbose_name="Страва", on_delete=models.PROTECT, related_name="menu_items")
    sort_order = models.PositiveIntegerField(default=0)
    history = HistoricalRecords()

    class Meta:
        verbose_name = "Страва в меню"
        verbose_name_plural = "Страви в меню"
        ordering = ["menu", "sort_order", "id"]

    def __str__(self):
        return f"{self.menu} · {self.get_meal_slot_display()} · {self.dish}"

    def clean(self):
        if self.dish_id and self.menu_id and not self.dish.diets.filter(pk=self.menu.diet_id).exists():
            raise ValidationError(
                {"dish": f"Страва «{self.dish.name}» не дозволена для дієти {self.menu.diet.code}."}
            )
