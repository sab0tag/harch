from django import forms
from django.core.exceptions import ValidationError

from .models import Diet, Dish, DishIngredient, Product


class DishForm(forms.ModelForm):
    class Meta:
        model = Dish
        fields = ["card_no", "name", "category", "output_g", "diets", "technology", "source", "is_active"]
        widgets = {
            "diets": forms.CheckboxSelectMultiple,
            "technology": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["diets"].queryset = Diet.objects.filter(is_active=True)
        self.fields["category"].widget.attrs["list"] = "dish-categories"
        for name, field in self.fields.items():
            if name not in ("diets", "is_active"):
                field.widget.attrs.setdefault("class", "form-control")


class IngredientForm(forms.ModelForm):
    class Meta:
        model = DishIngredient
        fields = ["product", "brutto_g", "netto_g"]

    def __init__(self, *args, dish=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.dish = dish or getattr(self.instance, "dish", None)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        self.fields["product"].widget.attrs["class"] = "form-select form-select-sm"
        for f in ("brutto_g", "netto_g"):
            self.fields[f].widget.attrs.update(
                {"class": "form-control form-control-sm text-end", "inputmode": "decimal", "step": "0.01"}
            )

    def clean(self):
        data = super().clean()
        brutto, netto, product = data.get("brutto_g"), data.get("netto_g"), data.get("product")
        if brutto is not None and netto is not None and netto > brutto:
            self.add_error("netto_g", "Нетто не може бути більше за брутто.")
        if product and self.dish:
            dup = DishIngredient.objects.filter(dish=self.dish, product=product).exclude(pk=self.instance.pk)
            if dup.exists():
                self.add_error("product", "Цей продукт уже є в картці.")
        return data


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "category", "unit", "protein", "fat", "carbs", "kcal", "price_per_kg", "is_active", "verified"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name not in ("is_active", "verified"):
                field.widget.attrs.setdefault("class", "form-select" if name == "category" else "form-control")


class ProductPriceForm(forms.ModelForm):
    """Accountants may change only the price."""

    class Meta:
        model = Product
        fields = ["price_per_kg"]
        widgets = {"price_per_kg": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"})}

    def clean_price_per_kg(self):
        value = self.cleaned_data["price_per_kg"]
        if value is not None and value < 0:
            raise ValidationError("Ціна не може бути від'ємною.")
        return value
