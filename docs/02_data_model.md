# 02. Модель данных

Все массы — граммы, `Decimal(12,2)`. Все справочные модели — `simple_history` + поля
`is_active`, `verified` (bool, по умолчанию False для seed), `created_at`, `updated_at`.

## accounts

```python
class User(AbstractUser):
    class Role(TextChoices):
        ADMIN = "admin", "Адміністратор"
        DIETICIAN = "dietician", "Дієтсестра / лікар-дієтолог"
        CHEF = "chef", "Завідувач виробництва"
        DEPT_NURSE = "dept_nurse", "Старша медсестра відділення"
        ACCOUNTANT = "accountant", "Бухгалтер"
    role = CharField(choices=Role.choices)
    department = ForeignKey("orgstructure.Department", null=True, blank=True)  # обязателен для DEPT_NURSE
    phone = CharField(blank=True)
    telegram_chat_id = CharField(blank=True)   # v0.2
    # constraint: role != DEPT_NURSE or department IS NOT NULL
```

## orgstructure

```python
class Department(Model):
    name = CharField(unique=True)
    patient_category = CharField(choices=[adults, children, maternity])  # Дорослі / Діти / Породіллі
    default_diet = ForeignKey("catalog.Diet")
    beds = PositiveIntegerField(null=True)
    sort_order = PositiveIntegerField(default=0)
```

## catalog

```python
class Diet(Model):
    code = CharField(unique=True)            # ОВД, ЩД, ВБД, НБД, НКД, №1а, №4, №9
    name = CharField()
    kind = CharField(choices=[variant, special])
    kcal_min, kcal_max = PositiveIntegerField(null=True) ×2
    protein_min, protein_max, fat_min, fat_max, carbs_min, carbs_max = Decimal(null=True) ×6
    indications = TextField(blank=True)
    meal_slots = ArrayField(CharField(choices=MealSlot.choices))  # какие приёмы пищи у дієти
    has_own_menus = BooleanField(default=True)  # №1а/№4 в пилоте False — только тег у страв

class MealSlot(TextChoices):
    BREAKFAST = "breakfast", "Сніданок"
    SECOND_BREAKFAST = "second_breakfast", "2-й сніданок"
    LUNCH = "lunch", "Обід"
    AFTERNOON_SNACK = "afternoon_snack", "Полуденок"
    DINNER = "dinner", "Вечеря"

class ProductCategory(Model):   # Крупи, Молочні, М'ясо, Овочі, ... (для зведень і закупівлі)
    name = CharField(unique=True)

class Product(Model):
    name = CharField(unique=True)
    category = ForeignKey(ProductCategory, null=True)
    unit = CharField(default="g")            # для отображения: г / мл / шт
    protein, fat, carbs, kcal = Decimal(6,2)  # на 100 г нетто
    price_per_kg = Decimal(10,2, null=True)  # текущая цена (ручной ввод или импорт S5-07); изменения — в simple-history

class Dish(Model):                           # картка-розкладка
    card_no = PositiveIntegerField(unique=True)
    name = CharField()
    category = CharField()                   # «Обід — супи» и т.п. (группировка в UI)
    output_g = Decimal                       # вихід на 1 порцію
    diets = ManyToManyField(Diet)            # для каких дієт допустима
    technology = TextField(blank=True)       # технологія приготування
    source = CharField(blank=True)           # збірник рецептур, сторінка
    origin = CharField(choices=[seed, manual, ai_import], default=manual)  # откуда картка (см. 07_ai.md)
    # вычисляемые (property, не хранить): protein/fat/carbs/kcal на порцию = Σ ingredient.netto × product.X / 100

class DishIngredient(Model):
    dish = ForeignKey(Dish, related_name="ingredients")
    product = ForeignKey(Product, on_delete=PROTECT)
    brutto_g = Decimal
    netto_g = Decimal
    sort_order = PositiveIntegerField
    # constraint: netto_g <= brutto_g; unique(dish, product)
```

`ProductSubstitution` и `SeasonalWasteNorm` — справочные таблицы из seed, только чтение в v0.1.

Импорт цен из Excel (S5-07, `apps/catalog/price_import.py`):

```python
class PriceImport(Model):          # один загруженный файл; сам файл не хранится, только ячейки
    filename, uploaded_by, created_at
    status = CharField(choices=[draft, applied, cancelled])
    sheets = JSONField             # {аркуш: [[текст ячейки, ...], ...]}, ≤ 10 аркушів × 3000 рядків × 30 стовпців
    sheet, header_row, name_col, price_col, unit_col   # сопоставление столбцов (0-based)
    unit = CharField(choices=[kg, 100g, g])            # в чём указана цена, если нет столбца одиниці
    applied_at, applied_by, applied_count

class PriceImportRow(Model):       # строка файла после сопоставления
    price_import = ForeignKey(PriceImport, related_name="rows")
    row_no, raw_name, raw_price, raw_unit
    price_per_kg = Decimal(10,2, null=True)            # приведено к грн/кг
    problem = CharField(blank=True)                    # не читается цена / «шт» и т.п. — применить нельзя
    product = ForeignKey(Product, null=True)
    match = CharField(choices=[alias, exact, fuzzy, manual, none])
    old_price, selected, applied

class ProductAlias(Model):         # «назва у файлі бухгалтерії» → продукт; пишется при подтверждении человеком
    name = CharField(unique=True)  # нормализованное (audit.normalize)
    product = ForeignKey(Product, on_delete=CASCADE)
```

## menus

```python
class Menu(Model):
    number = PositiveIntegerField(unique=True)
    diet = ForeignKey(Diet)                  # ИНВАРИАНТ: ровно одна дієта
    title = CharField(blank=True)
    is_active = BooleanField(default=True)

class MenuItem(Model):
    menu = ForeignKey(Menu, related_name="items")
    meal_slot = CharField(choices=MealSlot.choices)
    dish = ForeignKey(Dish, on_delete=PROTECT)
    sort_order = PositiveIntegerField
    # validation: dish.diets contains menu.diet (иначе ошибка формы)
```

## orders

```python
class DailyOrder(Model):                     # порційна вимога відділення на дату
    department = ForeignKey(Department)
    service_date = DateField()
    task = OneToOneField("workflow.Task", null=True)
    note = TextField(blank=True)
    unique_together = (department, service_date)

class DailyOrderLine(Model):
    order = ForeignKey(DailyOrder, related_name="lines")
    diet = ForeignKey(Diet)
    portions = PositiveIntegerField()
    unique_together = (order, diet)
```

Порції по дієті на дату = `SUM(lines.portions) WHERE order.service_date = D AND order.task.status = approved`.

## planning

```python
class DailyPlan(Model):                      # меню-розкладка на день
    service_date = DateField(unique=True)
    task = OneToOneField("workflow.Task", null=True)
    approved_at = DateTimeField(null=True)

class DailyPlanEntry(Model):
    plan = ForeignKey(DailyPlan, related_name="entries")
    diet = ForeignKey(Diet)
    menu = ForeignKey(Menu)                  # validation: menu.diet == diet
    portions = PositiveIntegerField()        # копируется из утверждённых замовлень при формировании
    unique_together = (plan, diet)
```

## consumption

```python
class ConsumptionSnapshotLine(Model):        # замороженный расчёт при approve плана
    plan = ForeignKey(DailyPlan, related_name="snapshot")
    diet = ForeignKey(Diet)
    menu_number = PositiveIntegerField()
    meal_slot = CharField()
    dish_card_no = PositiveIntegerField()
    dish_name = CharField()
    product = ForeignKey(Product, on_delete=PROTECT)
    product_name = CharField()               # копия на момент утверждения
    portions = PositiveIntegerField()
    brutto_per_portion_g = Decimal
    netto_per_portion_g = Decimal
    brutto_total_g = Decimal                 # = portions × brutto_per_portion_g
    netto_total_g = Decimal
    kcal_total = Decimal

class ActualConsumption(Model):              # факт от шеф-кухаря (отклонения от плана)
    plan = ForeignKey(DailyPlan)
    product = ForeignKey(Product)
    planned_brutto_g = Decimal               # сумма из снапшота
    actual_brutto_g = Decimal                # что реально ушло
    reason = CharField(blank=True)           # заміна, нестача, псування...
    unique_together = (plan, product)

class WriteOff(Model):                       # списання бухгалтерією
    plan = OneToOneField(DailyPlan)
    task = OneToOneField("workflow.Task")
    total_cost = Decimal(null=True)
```

## workflow

См. `03_task_engine.md`.

## Расчёт розходу — `apps/consumption/calc.py`

```python
@dataclass(frozen=True)
class Line:
    diet_code: str; menu_number: int; meal_slot: str
    dish_card_no: int; dish_name: str
    product_id: int; product_name: str
    portions: int; brutto_pp: Decimal; netto_pp: Decimal; kcal_per_100: Decimal

def compute_plan_lines(plan: DailyPlan) -> list[Line]: ...
def totals_by_product(lines) -> dict[product_id, (brutto_g, netto_g)]: ...
def menu_nutrition(menu: Menu) -> Nutrition(protein, fat, carbs, kcal)   # на 1 человека в день
```

Одна и та же страва может стоять в меню дважды (например, хліб на сніданок и обід) — это
две разные строки, суммируются.

### Контрольный пример (из seed, использовать в unit-тесте)

План: ОВД → меню №1 × 10 порцій, ЩД → меню №81 × 5 порцій.

| Продукт | Брутто всього, г |
|---|---|
| Молоко 2.5% | 2850 |
| Масло вершкове | 235 |
| Цукор | 675 |
| Картопля | 5700 |
| Хліб білий | 1900 |

Всего 30 разных продуктов, сумма брутто 40 835 г.
Калорийность на 1 человека: меню №1 = 1980.8 ккал, меню №81 = 2034.9 ккал.

## ⚠ Известная проблема seed-меню

Проверка 250 сгенерированных меню против норм ккал: **234 из 250 ниже нижней границы нормы**
(например, ОВД: 72 из 80 ниже 2170 ккал; НКД/НБД/№9 — все ниже). Структура меню корректна,
но порции занижены. Поэтому:

- все seed-меню грузятся с `verified=False`;
- экран меню показывает расчётные ккал/БЖУ vs норма дієти с цветовой индикацией;
- дієтсестра корректирует картки/меню в системе — это часть пилота, а не баг кода.
