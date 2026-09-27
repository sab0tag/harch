from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Diet, Dish, DishIngredient, Product, ProductCategory, ProductSubstitution, SeasonalWasteNorm


@admin.register(Diet)
class DietAdmin(SimpleHistoryAdmin):
    list_display = ["code", "name", "kind", "kcal_min", "kcal_max", "has_own_menus", "is_active"]
    list_filter = ["kind", "is_active"]


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    search_fields = ["name"]


@admin.register(Product)
class ProductAdmin(SimpleHistoryAdmin):
    list_display = ["name", "category", "protein", "fat", "carbs", "kcal", "price_per_kg", "verified"]
    list_filter = ["category", "verified", "is_active"]
    search_fields = ["name"]


class DishIngredientInline(admin.TabularInline):
    model = DishIngredient
    extra = 0
    autocomplete_fields = ["product"]


@admin.register(Dish)
class DishAdmin(SimpleHistoryAdmin):
    list_display = ["card_no", "name", "category", "output_g", "origin", "verified", "is_active"]
    list_filter = ["category", "diets", "origin", "verified", "is_active"]
    search_fields = ["name", "card_no"]
    filter_horizontal = ["diets"]
    inlines = [DishIngredientInline]


@admin.register(ProductSubstitution)
class ProductSubstitutionAdmin(admin.ModelAdmin):
    list_display = ["basis", "product", "qty_g", "substitute", "substitute_qty_g"]
    list_filter = ["basis"]


@admin.register(SeasonalWasteNorm)
class SeasonalWasteNormAdmin(admin.ModelAdmin):
    list_display = ["product", "until_jan1", "jan_feb", "mar_apr", "may_aug", "sep_dec"]
