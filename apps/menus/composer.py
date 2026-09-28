"""AI-08: menu combinations that meet a diet's norm. Pure optimisation in code — no AI here.

A menu is a list of positions (meal slot, dish category) → dish. Candidates for a position are
the active dishes allowed for the diet with the same category, so a soup is only swapped for a
soup and every dish stays allowed for the diet (MenuItem.clean). The model is used afterwards,
optionally, only to comment on combinations (apps/assist/features.py → review_menu).

Score = weighted squared relative distance of kcal/protein/fat/carbs to the diet's [min, max]
(0 inside the range) + penalties for repeating a dish in a day and for copying an existing menu.
"""
import random
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from apps.catalog.models import MealSlot
from apps.catalog.nutrition import with_dish_nutrition

from .models import Menu, MenuItem

FIELDS = ("kcal", "protein", "fat", "carbs")
WEIGHTS = {"kcal": 3.0, "protein": 1.5, "fat": 1.0, "carbs": 1.0}
REPEAT_OK = ("хліб", "напої", "додатки")          # bread, drinks may appear twice a day
EXTENDABLE = ("хліб", "додатки", "2-й сніданок", "полуденок")   # what "add a dish" may add
REPEAT_PENALTY = 0.5
COPY_PENALTY = 0.3                                  # new menus should not clone existing ones
SLOT_ORDER = {v: i for i, (v, _) in enumerate(MealSlot.choices)}


@dataclass(frozen=True)
class DishInfo:
    pk: int
    name: str
    category: str
    n: tuple   # kcal, protein, fat, carbs per portion (floats)


@dataclass
class Plan:
    """A concrete menu: positions [(slot, category)] and chosen dishes (same length)."""

    positions: list
    dishes: list
    changes: list = field(default_factory=list)   # human-readable steps for «Підігнати»

    def totals(self):
        t = [0.0, 0.0, 0.0, 0.0]
        for d in self.dishes:
            for i in range(4):
                t[i] += d.n[i]
        return dict(zip(FIELDS, t))


class Composer:
    def __init__(self, diet):
        self.diet = diet
        dishes = with_dish_nutrition(diet.dishes.filter(is_active=True).order_by("name"))
        self.dishes = {
            d.pk: DishInfo(d.pk, d.name, d.category,
                           (float(d.n_kcal), float(d.n_protein), float(d.n_fat), float(d.n_carbs)))
            for d in dishes
        }
        self.by_category = {}
        for d in self.dishes.values():
            self.by_category.setdefault(d.category, []).append(d)
        self.norm = {f: tuple(None if v is None else float(v) for v in diet.norm(f)) for f in FIELDS}
        self.existing = [set(m.items.values_list("dish_id", flat=True)) for m in diet.menus.filter(is_active=True)]

    # ------------------------------------------------------------------ scoring
    def deviation(self, totals):
        out = {}
        for f in FIELDS:
            low, high = self.norm[f]
            v = totals[f]
            if low is not None and v < low:
                out[f] = (low - v) / low
            elif high is not None and v > high:
                out[f] = (v - high) / high
            else:
                out[f] = 0.0
        return out

    def score(self, plan, *, novelty=False):
        dev = self.deviation(plan.totals())
        s = sum(WEIGHTS[f] * dev[f] ** 2 for f in FIELDS) * 100
        counts = Counter(d.pk for d in plan.dishes)
        for d in plan.dishes:
            if counts[d.pk] > 1 and not any(k in d.category.lower() for k in REPEAT_OK):
                s += REPEAT_PENALTY
        if novelty and self.existing:
            chosen = {d.pk for d in plan.dishes}
            best = max(len(chosen & e) / len(chosen | e) for e in self.existing)
            if best > 0.6:
                s += COPY_PENALTY * (best - 0.6) / 0.4 * 10
        return s

    def in_norm(self, plan):
        return all(v == 0 for v in self.deviation(plan.totals()).values())

    # ------------------------------------------------------------------ templates
    def template(self):
        """Most common structure of this diet's menus; ОВД's if the diet has none."""
        menus = list(self.diet.menus.filter(is_active=True).prefetch_related("items__dish"))
        if not menus:
            menus = list(Menu.objects.filter(diet__code="ОВД", is_active=True).prefetch_related("items__dish")[:40])
        sigs = Counter(
            tuple((i.meal_slot, i.dish.category) for i in m.items.all()) for m in menus
        )
        positions = list(sigs.most_common(1)[0][0]) if sigs else []
        return [p for p in positions if p[1] in self.by_category]

    def from_menu(self, menu):
        items = [i for i in menu.items.select_related("dish").order_by("sort_order", "id")]
        return Plan([(i.meal_slot, i.dish.category) for i in items],
                    [self.dishes.get(i.dish_id) or _info(i.dish) for i in items])

    # ------------------------------------------------------------------ search
    def improve(self, plan, *, max_changes=3, max_adds=2, novelty=False):
        """Greedy best-improvement: each step one swap (same category) or one addition."""
        steps, adds = [], 0
        current = Plan(list(plan.positions), list(plan.dishes))
        best_score = self.score(current, novelty=novelty)
        for _ in range(max_changes):
            if self.in_norm(current) and best_score < 0.01:
                break
            best = None
            for i, (slot, cat) in enumerate(current.positions):
                for cand in self.by_category.get(cat, []):
                    if cand.pk == current.dishes[i].pk:
                        continue
                    trial = Plan(current.positions, current.dishes[:i] + [cand] + current.dishes[i + 1:])
                    sc = self.score(trial, novelty=novelty)
                    if sc < best_score - 1e-6 and (best is None or sc < best[0]):
                        best = (sc, trial, ("swap", slot, current.dishes[i], cand))
            if adds < max_adds:
                for slot, cat in sorted({p for p in current.positions}, key=lambda p: SLOT_ORDER.get(p[0], 9)):
                    if not any(k in cat.lower() for k in EXTENDABLE):
                        continue
                    for cand in self.by_category.get(cat, []):
                        trial = Plan(current.positions + [(slot, cat)], current.dishes + [cand])
                        sc = self.score(trial, novelty=novelty)
                        # an addition must pay for itself clearly: prefer swaps
                        if sc < best_score - 0.5 and (best is None or sc < best[0] - 0.2):
                            best = (sc, trial, ("add", slot, None, cand))
            if best is None:
                break
            best_score, current, step = best
            if step[0] == "add":
                adds += 1
            steps.append(step)
        current.changes = steps
        return current

    def fit_options(self, menu):
        """Up to three proposals for an existing menu: with 1, 2 and up to 4 changes."""
        base = self.from_menu(menu)
        options, seen = [], set()
        for limit in (1, 3, 6):
            p = self.improve(base, max_changes=limit, max_adds=min(2, limit - 1))
            key = tuple(d.pk for d in p.dishes)
            if p.changes and key not in seen:
                seen.add(key)
                options.append(p)
        return base, options

    def generate(self, count=3, seed=None, tries=12):
        rng = random.Random(seed)
        positions = self.template()
        results = []
        for _ in range(tries):
            dishes = [rng.choice(self.by_category[c]) for _, c in positions]
            p = self.improve(Plan(positions, dishes), max_changes=25, max_adds=2, novelty=True)
            p.changes = []
            results.append((self.score(p, novelty=True), p))
        results.sort(key=lambda r: r[0])
        chosen, keys = [], []
        for _, p in results:
            key = {d.pk for d in p.dishes}
            if all(len(key & k) / len(key | k) < 0.75 for k in keys):
                chosen.append(p)
                keys.append(key)
            if len(chosen) == count:
                break
        return chosen

    # ------------------------------------------------------------------ presentation
    def describe(self, plan):
        totals = plan.totals()
        dev = self.deviation(totals)
        rows = []
        for f, label in (("kcal", "Ккал"), ("protein", "Білки"), ("fat", "Жири"), ("carbs", "Вуглеводи")):
            low, high = self.norm[f]
            rows.append({"label": label, "value": totals[f], "low": low, "high": high, "ok": dev[f] == 0})
        slots = {}
        for (slot, _), d in sorted(zip(plan.positions, plan.dishes), key=lambda x: SLOT_ORDER.get(x[0][0], 9)):
            slots.setdefault(slot, []).append(d)
        return {
            "rows": rows,
            "in_norm": self.in_norm(plan),
            "slots": [(dict(MealSlot.choices)[s], s, ds) for s, ds in slots.items()],
            "changes": [
                {"kind": k, "slot": dict(MealSlot.choices).get(s, s), "old": old, "new": new}
                for k, s, old, new in plan.changes
            ],
            "items": [[s, d.pk] for (s, _), d in zip(plan.positions, plan.dishes)],
        }

    def reachable_hint(self):
        """If even the best search can't reach the norm, say which value and suggest new cards."""
        best = self.generate(count=1, seed=1, tries=6)
        if not best or self.in_norm(best[0]):
            return None
        dev = self.deviation(best[0].totals())
        short = [f for f in FIELDS if dev[f] > 0]
        return short


def _info(dish):
    from apps.catalog.nutrition import dish_nutrition

    n = dish_nutrition(dish)
    return DishInfo(dish.pk, dish.name, dish.category, (float(n.kcal), float(n.protein), float(n.fat), float(n.carbs)))


# ====================================================================== saving
def validate_items(diet, items):
    """items: [[slot, dish_id], ...] from the form. Returns [(slot, Dish)] or raises ValueError."""
    from apps.catalog.models import Dish

    slots = dict(MealSlot.choices)
    allowed = {d.pk: d for d in diet.dishes.filter(is_active=True)}
    result = []
    for entry in items:
        if not (isinstance(entry, (list, tuple)) and len(entry) == 2):
            raise ValueError("Невірний формат меню.")
        slot, pk = entry
        if slot not in slots or pk not in allowed:
            raise ValueError("Страва не дозволена для дієти або невідомий прийом їжі.")
        result.append((slot, allowed[pk]))
    if not result:
        raise ValueError("Меню порожнє.")
    return result


def save_new_menu(diet, items, title=""):
    from django.db import transaction
    from django.db.models import Max

    rows = validate_items(diet, items)
    with transaction.atomic():
        number = (Menu.objects.select_for_update().aggregate(m=Max("number"))["m"] or 0) + 1
        menu = Menu.objects.create(number=number, diet=diet, title=title[:200], verified=False)
        for i, (slot, dish) in enumerate(sorted(rows, key=lambda r: SLOT_ORDER.get(r[0], 9))):
            MenuItem.objects.create(menu=menu, meal_slot=slot, dish=dish, sort_order=i)
    return menu


def replace_items(menu, items):
    """Apply a proposal to an existing menu (history is kept by simple-history); needs re-verification."""
    from django.db import transaction

    rows = validate_items(menu.diet, items)
    with transaction.atomic():
        for item in menu.items.all():
            item.delete()                     # per-row delete → history records
        for i, (slot, dish) in enumerate(sorted(rows, key=lambda r: SLOT_ORDER.get(r[0], 9))):
            MenuItem.objects.create(menu=menu, meal_slot=slot, dish=dish, sort_order=i)
        menu.verified = False
        menu.save(update_fields=["verified", "updated_at"])
    return menu
