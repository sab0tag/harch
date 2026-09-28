from decimal import Decimal

from django.contrib.postgres.fields import ArrayField
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from simple_history.models import HistoricalRecords

GRAMS = {"max_digits": 12, "decimal_places": 2, "validators": [MinValueValidator(Decimal("0"))]}
PER_100 = {"max_digits": 7, "decimal_places": 2, "validators": [MinValueValidator(Decimal("0"))]}


class MealSlot(models.TextChoices):
    BREAKFAST = "breakfast", "Сніданок"
    SECOND_BREAKFAST = "second_breakfast", "2-й сніданок"
    LUNCH = "lunch", "Обід"
    AFTERNOON_SNACK = "afternoon_snack", "Полуденок"
    DINNER = "dinner", "Вечеря"


class Origin(models.TextChoices):
    SEED = "seed", "Стартові дані"
    MANUAL = "manual", "Внесено вручну"
    AI_IMPORT = "ai_import", "Імпорт ШІ"
    AI_DRAFT = "ai_draft", "Чернетка ШІ"
    AI_ESTIMATE = "ai_estimate", "Оцінка ШІ"


class ReferenceModel(models.Model):
    """Common fields for reference data (docs/02_data_model.md)."""

    is_active = models.BooleanField("Активний", default=True)
    verified = models.BooleanField("Перевірено", default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


def all_meal_slots():
    return list(MealSlot.values)


class Diet(ReferenceModel):
    class Kind(models.TextChoices):
        VARIANT = "variant", "Варіант дієти"
        SPECIAL = "special", "Спеціальна дієта"

    code = models.CharField("Код", max_length=10, unique=True)
    name = models.CharField("Назва", max_length=200)
    kind = models.CharField("Тип", max_length=10, choices=Kind.choices)
    kcal_min = models.PositiveIntegerField("Ккал від", null=True, blank=True)
    kcal_max = models.PositiveIntegerField("Ккал до", null=True, blank=True)
    protein_min = models.DecimalField("Білки від, г", max_digits=6, decimal_places=1, null=True, blank=True)
    protein_max = models.DecimalField("Білки до, г", max_digits=6, decimal_places=1, null=True, blank=True)
    fat_min = models.DecimalField("Жири від, г", max_digits=6, decimal_places=1, null=True, blank=True)
    fat_max = models.DecimalField("Жири до, г", max_digits=6, decimal_places=1, null=True, blank=True)
    carbs_min = models.DecimalField("Вуглеводи від, г", max_digits=6, decimal_places=1, null=True, blank=True)
    carbs_max = models.DecimalField("Вуглеводи до, г", max_digits=6, decimal_places=1, null=True, blank=True)
    indications = models.TextField("Показання", blank=True)
    meal_slots = ArrayField(
        models.CharField(max_length=20, choices=MealSlot.choices),
        verbose_name="Прийоми їжі",
        default=all_meal_slots,
    )
    has_own_menus = models.BooleanField("Має власні меню", default=True)
    sort_order = models.PositiveIntegerField("Порядок", default=0)
    history = HistoricalRecords()

    class Meta:
        verbose_name = "Дієта"
        verbose_name_plural = "Дієти"
        ordering = ["sort_order", "code"]

    def __str__(self):
        return self.code

    def norm(self, field):
        """(min, max) for 'kcal' | 'protein' | 'fat' | 'carbs'; (None, None) if not set."""
        return getattr(self, f"{field}_min"), getattr(self, f"{field}_max")


class ProductCategory(models.Model):
    name = models.CharField("Назва", max_length=100, unique=True)

    class Meta:
        verbose_name = "Категорія продуктів"
        verbose_name_plural = "Категорії продуктів"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(ReferenceModel):
    name = models.CharField("Назва", max_length=150, unique=True)
    category = models.ForeignKey(
        ProductCategory, verbose_name="Категорія", null=True, blank=True, on_delete=models.SET_NULL
    )
    unit = models.CharField("Одиниця", max_length=10, default="г")
    protein = models.DecimalField("Білки / 100 г", **PER_100)
    fat = models.DecimalField("Жири / 100 г", **PER_100)
    carbs = models.DecimalField("Вуглеводи / 100 г", **PER_100)
    kcal = models.DecimalField("Ккал / 100 г", **PER_100)
    origin = models.CharField("Походження", max_length=12, choices=Origin.choices, default=Origin.MANUAL)
    source = models.CharField("Джерело значень", max_length=200, blank=True)
    price_per_kg = models.DecimalField(
        "Ціна за кг, грн", max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    history = HistoricalRecords()

    class Meta:
        verbose_name = "Продукт"
        verbose_name_plural = "Продукти"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Dish(ReferenceModel):
    """Картка-розкладка: постійна рецептура на 1 порцію."""

    card_no = models.PositiveIntegerField("№ картки", unique=True)
    name = models.CharField("Назва", max_length=200)
    category = models.CharField("Категорія", max_length=100, blank=True)
    output_g = models.DecimalField("Вихід, г", **GRAMS)
    diets = models.ManyToManyField(Diet, verbose_name="Дієти", related_name="dishes", blank=True)
    technology = models.TextField("Технологія приготування", blank=True)
    source = models.CharField("Джерело рецептури", max_length=200, blank=True)
    origin = models.CharField("Походження", max_length=12, choices=Origin.choices, default=Origin.MANUAL)
    history = HistoricalRecords(m2m_fields=[diets])

    class Meta:
        verbose_name = "Картка-розкладка"
        verbose_name_plural = "Картки-розкладки"
        ordering = ["card_no"]

    def __str__(self):
        return f"№{self.card_no} {self.name}"


class DishIngredient(models.Model):
    dish = models.ForeignKey(Dish, related_name="ingredients", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, verbose_name="Продукт", on_delete=models.PROTECT)
    brutto_g = models.DecimalField("Брутто, г", **GRAMS)
    netto_g = models.DecimalField("Нетто, г", **GRAMS)
    sort_order = models.PositiveIntegerField(default=0)
    history = HistoricalRecords()

    class Meta:
        verbose_name = "Інгредієнт"
        verbose_name_plural = "Інгредієнти"
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["dish", "product"], name="dish_product_unique"),
            models.CheckConstraint(condition=Q(netto_g__lte=F("brutto_g")), name="netto_lte_brutto"),
        ]

    def __str__(self):
        return f"{self.product} {self.brutto_g}/{self.netto_g} г"

    @property
    def waste_pct(self):
        if not self.brutto_g:
            return Decimal("0")
        return (self.brutto_g - self.netto_g) / self.brutto_g * 100


class ProductSubstitution(models.Model):
    """Заміна продуктів (додатки 1, 2 наказу №931). Довідково."""

    class Basis(models.TextChoices):
        PROTEIN = "protein", "За білками"
        CARBS = "carbs", "За вуглеводами"

    basis = models.CharField("Основа", max_length=10, choices=Basis.choices)
    product = models.CharField("Продукт", max_length=150)
    qty_g = models.DecimalField("Кількість, г", max_digits=8, decimal_places=2)
    substitute = models.CharField("Заміна", max_length=150)
    substitute_qty_g = models.DecimalField("Кількість заміни, г", max_digits=8, decimal_places=2)

    class Meta:
        verbose_name = "Заміна продукту"
        verbose_name_plural = "Заміни продуктів"
        ordering = ["basis", "product", "substitute"]
        constraints = [
            models.UniqueConstraint(fields=["basis", "product", "substitute"], name="substitution_unique"),
        ]

    def __str__(self):
        return f"{self.product} {self.qty_g} г → {self.substitute} {self.substitute_qty_g} г"


class SeasonalWasteNorm(models.Model):
    """Сезонні норми відходів при холодній обробці. Довідково, як у документі."""

    product = models.CharField("Продукт", max_length=150, unique=True)
    until_jan1 = models.CharField("До 1 січня", max_length=30, blank=True)
    jan_feb = models.CharField("Січень–лютий", max_length=30, blank=True)
    mar_apr = models.CharField("Березень–квітень", max_length=30, blank=True)
    may_aug = models.CharField("Травень–серпень", max_length=30, blank=True)
    sep_dec = models.CharField("Вересень–грудень", max_length=30, blank=True)

    class Meta:
        verbose_name = "Сезонна норма відходів"
        verbose_name_plural = "Сезонні норми відходів"
        ordering = ["product"]

    def __str__(self):
        return self.product


class ReferenceFinding(models.Model):
    """Зауваження нічної перевірки довідника (AI-09). Правила — код; ШІ лише групує дублі."""

    class Severity(models.TextChoices):
        ERROR = "error", "Помилка"
        WARN = "warn", "Підозріло"
        INFO = "info", "До уваги"

    class Status(models.TextChoices):
        OPEN = "open", "Відкрите"
        DISMISSED = "dismissed", "Не проблема"
        RESOLVED = "resolved", "Виправлено"

    kind = models.CharField("тип", max_length=40)
    severity = models.CharField("важливість", max_length=10, choices=Severity.choices)
    obj_type = models.CharField(max_length=20)          # product / dish / menu
    obj_id = models.PositiveIntegerField()
    title = models.CharField("об'єкт", max_length=200)
    message = models.CharField("опис", max_length=500)
    details = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=10, default="rule")  # rule / ai
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    note = models.CharField("коментар", max_length=300, blank=True)
    dismissed_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Зауваження до довідника"
        verbose_name_plural = "Перевірка довідника"
        ordering = ["status", "severity", "kind", "title"]
        constraints = [
            models.UniqueConstraint(fields=["kind", "obj_type", "obj_id"], name="uniq_finding_per_object"),
        ]

    def __str__(self):
        return f"{self.get_severity_display()}: {self.title} — {self.message}"
