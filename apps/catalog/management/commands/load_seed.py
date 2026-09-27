"""Load starter reference data from seed/*.json (extracted from the Excel prototype).

Idempotent and non-destructive: records that already exist (matched by natural key) are
left untouched, so a re-run never overwrites what the dietician has edited. Everything
created here is marked verified=False (and Dish.origin=seed).
"""
import json
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from simple_history.utils import bulk_create_with_history

from apps.catalog.models import (
    Diet, Dish, DishIngredient, MealSlot, Origin, Product, ProductSubstitution, SeasonalWasteNorm,
)
from apps.menus.models import Menu, MenuItem
from apps.orgstructure.models import Department

PATIENT_CATEGORIES = {
    "Дорослі": Department.PatientCategory.ADULTS,
    "Діти": Department.PatientCategory.CHILDREN,
    "Породіллі": Department.PatientCategory.MATERNITY,
}
MEAL_ORDER = list(MealSlot.values)


def D(value):
    return None if value is None else Decimal(str(value))


class Command(BaseCommand):
    help = "Load seed/*.json reference data (idempotent, never overwrites existing records)."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", default="seed", help="Folder with seed JSON files")

    def handle(self, *args, path, **options):
        self.root = Path(path)
        if not self.root.is_dir():
            raise CommandError(f"Seed folder not found: {self.root}")
        self.stats = {}
        self.warnings = []
        with transaction.atomic():
            self.load_diets()
            self.load_products()
            self.load_departments()
            self.load_dishes()
            self.load_menus()
            self.load_reference()
        for name, (created, existing) in self.stats.items():
            self.stdout.write(f"{name:<14} created {created:>4}   already existed {existing:>4}")
        for w in self.warnings:
            self.stdout.write(self.style.WARNING(f"WARNING: {w}"))
        self.stdout.write(self.style.SUCCESS("Seed loaded."))

    # ------------------------------------------------------------------ helpers
    def read(self, name):
        file = self.root / name
        if not file.exists():
            raise CommandError(f"Missing seed file: {file}")
        return json.loads(file.read_text(encoding="utf-8"))

    def count(self, name, created):
        c, e = self.stats.get(name, (0, 0))
        self.stats[name] = (c + created, e + (not created))

    # ------------------------------------------------------------------ loaders
    def load_diets(self):
        menu_diets = {m["diet"] for m in self.read("menus.json")}
        for i, d in enumerate(self.read("diets.json")):
            kcal = d.get("kcal") or [None, None]
            pr, ft, cb = (d.get(k) or [None, None] for k in ("protein_g", "fat_g", "carbs_g"))
            _, created = Diet.objects.get_or_create(
                code=d["code"],
                defaults=dict(
                    name=d["name"], kind=d["kind"], indications=d.get("indications") or "",
                    kcal_min=kcal[0], kcal_max=kcal[1],
                    protein_min=D(pr[0]), protein_max=D(pr[1]), fat_min=D(ft[0]), fat_max=D(ft[1]),
                    carbs_min=D(cb[0]), carbs_max=D(cb[1]),
                    has_own_menus=d["code"] in menu_diets, sort_order=i, verified=False,
                ),
            )
            self.count("diets", created)
        self.diets = {d.code: d for d in Diet.objects.all()}

    def load_products(self):
        for p in self.read("products.json"):
            _, created = Product.objects.get_or_create(
                name=p["name"],
                defaults=dict(protein=D(p["protein"]), fat=D(p["fat"]), carbs=D(p["carbs"]),
                              kcal=D(p["kcal"]), verified=False),
            )
            self.count("products", created)
        self.products = {p.name: p for p in Product.objects.all()}

    def load_departments(self):
        for i, d in enumerate(self.read("departments.json")):
            diet = self.diets.get(d["default_diet"])
            if diet is None:
                self.warnings.append(f"Department {d['name']}: unknown diet {d['default_diet']}")
            dept, created = Department.objects.get_or_create(
                name=d["name"],
                defaults=dict(patient_category=PATIENT_CATEGORIES.get(d["patient_category"], "adults"),
                              default_diet=diet, sort_order=i),
            )
            if not created and dept.default_diet_id is None and diet is not None:
                dept.default_diet = diet  # filling an empty field is not an overwrite
                dept.save(update_fields=["default_diet", "updated_at"])
            self.count("departments", created)

    def load_dishes(self):
        self.new_ingredients = []
        for d in self.read("dishes.json"):
            dish, created = Dish.objects.get_or_create(
                card_no=d["card_no"],
                defaults=dict(name=d["name"], category=d.get("category", ""), output_g=D(d["output_g"]),
                              origin=Origin.SEED, verified=False),
            )
            self.count("dishes", created)
            if not created:
                continue
            unknown = [c for c in d["diets"] if c not in self.diets]
            if unknown:
                self.warnings.append(f"Dish №{d['card_no']}: unknown diets {unknown}")
            dish.diets.set([self.diets[c] for c in d["diets"] if c in self.diets])
            for order, ing in enumerate(d["ingredients"]):
                product = self.products.get(ing["product"])
                if product is None:
                    raise CommandError(f"Dish №{d['card_no']}: unknown product {ing['product']!r}")
                self.new_ingredients.append(DishIngredient(
                    dish=dish, product=product, brutto_g=D(ing["brutto_g"]),
                    netto_g=D(ing["netto_g"]), sort_order=order,
                ))
        bulk_create_with_history(self.new_ingredients, DishIngredient, batch_size=500)
        self.dishes = {d.card_no: d for d in Dish.objects.prefetch_related("diets")}

    def load_menus(self):
        data = self.read("menus.json")
        existing = set(Menu.objects.values_list("number", flat=True))
        fresh = [m for m in data if m["number"] not in existing]
        for m in data:
            self.count("menus", m["number"] not in existing)
        menus = bulk_create_with_history(
            [Menu(number=m["number"], diet=self.diets[m["diet"]], verified=False) for m in fresh],
            Menu, batch_size=500,
        )
        by_number = {m.number: m for m in Menu.objects.filter(number__in=[m["number"] for m in fresh])}
        new_items = []
        for m in fresh:
            menu = by_number[m["number"]]
            diet = self.diets[m["diet"]]
            order = 0
            for slot in MEAL_ORDER:
                for card_no in m["meals"].get(slot, []):
                    dish = self.dishes.get(card_no)
                    if dish is None:
                        raise CommandError(f"Menu №{m['number']}: unknown dish card №{card_no}")
                    if diet not in dish.diets.all():  # prefetched
                        self.warnings.append(
                            f"Menu №{m['number']} ({diet.code}): dish №{card_no} «{dish.name}» not tagged for this diet"
                        )
                    new_items.append(MenuItem(menu=menu, meal_slot=slot, dish=dish, sort_order=order))
                    order += 1
        del menus
        bulk_create_with_history(new_items, MenuItem, batch_size=1000)

    def load_reference(self):
        for s in self.read("substitutions.json"):
            _, created = ProductSubstitution.objects.get_or_create(
                basis=s["basis"], product=s["product"], substitute=s["substitute"],
                defaults=dict(qty_g=D(s["qty_g"]), substitute_qty_g=D(s["substitute_qty_g"])),
            )
            self.count("substitutions", created)
        for s in self.read("seasonal_waste.json"):
            _, created = SeasonalWasteNorm.objects.get_or_create(
                product=s["product"],
                defaults={k: s.get(k) or "" for k in ("until_jan1", "jan_feb", "mar_apr", "may_aug", "sep_dec")},
            )
            self.count("seasonal", created)
