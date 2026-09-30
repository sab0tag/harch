"""AI features. Each one: build the prompt, call client.extract, validate the answer in code,
return drafts. Nothing here saves reference data — the views do, only after a human accepts.
"""
from apps.catalog.models import Product

from . import client

# ============================================================ AI-09 duplicate grouping
DUPLICATES_SCHEMA = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "integer"}, "minItems": 2},
                    "reason": {"type": "string", "description": "Коротко українською, чому це може бути один продукт"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["ids", "reason", "confidence"],
            },
        }
    },
    "required": ["groups"],
}

DUPLICATES_PROMPT = """You review the product catalogue of a Ukrainian hospital kitchen.
Find groups of entries that are very likely the SAME purchasable product written differently
(synonyms, spelling variants, a generic and a specific name used interchangeably in recipes).
Different fat content, grade, species or processing (e.g. "Молоко 2.5%" vs "Молоко 3.2%",
"Філе куряче" vs "Курятина", "Олія соняшникова" vs "Олія оливкова") are DIFFERENT products — do not group them
unless the names clearly denote the same thing. Prefer returning nothing over guessing.
Answer only with the tool. Reasons in Ukrainian, one short sentence."""


def ai_duplicate_groups(user=None):
    from apps.catalog.audit import Found

    products = list(Product.objects.filter(is_active=True).order_by("name").values_list("pk", "name"))
    listing = "\n".join(f"{pk}\t{name}" for pk, name in products)
    data = client.extract(
        user=user, feature="reference_duplicates", tool_name="report_duplicates", tool_schema=DUPLICATES_SCHEMA,
        system_prompt=DUPLICATES_PROMPT, max_tokens=2000,
        content_blocks=[{"type": "text", "text": f"id\tназва\n{listing}"}],
        request_meta={"products": len(products)},
    )
    names = dict(products)
    found = []
    for g in data.get("groups", []):
        ids = [i for i in dict.fromkeys(g.get("ids", [])) if i in names]   # drop unknown ids, keep order
        if len(ids) < 2 or g.get("confidence") == "low":
            continue
        found.append(Found(
            "product_duplicate_ai", "warn", "product", ids[0], names[ids[0]],
            f"ШІ: можливо, той самий продукт, що й {', '.join('«' + names[i] + '»' for i in ids[1:])}. {g.get('reason', '')}".strip(),
            {"ids": ids, "names": [names[i] for i in ids], "confidence": g.get("confidence")}, source="ai",
        ))
    return found


# ============================================================ AI-08 menu combination review
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["ok", "minor", "problems"]},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "meal": {"type": "string", "description": "Прийом їжі або 'день'"},
                    "text": {"type": "string", "description": "Що не так і що замінити, українською, 1 речення"},
                },
                "required": ["meal", "text"],
            },
        },
        "summary": {"type": "string", "description": "1–2 речення українською"},
    },
    "required": ["verdict", "issues", "summary"],
}

REVIEW_PROMPT = """You are an experienced hospital dietitian in Ukraine reviewing ONE day's menu for one diet
(Наказ МОЗ України №931). Nutrient totals are already checked by software — do NOT recalculate or
comment on calories, grams or norms. Judge only what software can't: whether dishes go together,
variety within the day (e.g. two porridges or two potato dishes, three dairy items), sensible
order of meals, and whether any dish looks unsuitable for the diet's indications.
Be concise and practical; if the menu is fine, say so with verdict "ok" and no issues.
Answer only with the tool, in Ukrainian."""


def review_menu(user, diet, items):
    from apps.catalog.models import MealSlot
    from apps.menus.composer import validate_items

    rows = validate_items(diet, items)
    labels = dict(MealSlot.choices)
    lines = [f"{labels[slot]}: {dish.name} ({dish.category})" for slot, dish in rows]
    text = (f"Дієта: {diet.code} — {diet.name}\nПоказання: {diet.indications or '—'}\n\nМеню:\n" + "\n".join(lines))
    data = client.extract(
        user=user, feature="menu_review", tool_name="review", tool_schema=REVIEW_SCHEMA,
        system_prompt=REVIEW_PROMPT, max_tokens=1200, content_blocks=[{"type": "text", "text": text}],
        request_meta={"diet": diet.code, "items": len(rows)},
    )
    verdict = data.get("verdict") if data.get("verdict") in ("ok", "minor", "problems") else "minor"
    issues = [i for i in data.get("issues", []) if isinstance(i, dict) and i.get("text")][:10]
    return {"verdict": verdict, "issues": issues, "summary": str(data.get("summary", ""))[:600]}


# ============================================================ AI-07 dish card draft
DISH_SCHEMA = {
    "type": "object",
    "properties": {
        "ingredients": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer"},
                    "brutto_g": {"type": "number", "description": "на 1 порцію, як закуплено"},
                    "netto_g": {"type": "number", "description": "на 1 порцію після холодної обробки"},
                },
                "required": ["product_id", "brutto_g", "netto_g"],
            },
        },
        "output_g": {"type": ["number", "null"], "description": "вихід готової страви на 1 порцію"},
        "technology": {"type": "string", "description": "3–6 речень українською"},
        "missing_products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["name", "reason"],
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ingredients", "technology", "missing_products", "warnings"],
}

DISH_PROMPT = """You draft a technological card (картка-розкладка) for a Ukrainian hospital kitchen
(Наказ МОЗ №931, typical Ukrainian/Soviet catering recipe collections). Quantities are grams for ONE portion:
brutto = as purchased, netto = after cold processing (netto ≤ brutto). Use ONLY products from the given list,
referenced by id. If an essential ingredient is not in the list, do NOT substitute silently — put it in
missing_products. Respect the selected diets: e.g. ЩД/№1а — no frying, no coarse fibre, steamed or boiled;
№9 — no added sugar (use the sugar substitute if listed); НКД — lean; НБД — little protein.
Do not estimate nutrients — software calculates them. Technology: concise steps in Ukrainian.
Put any doubts in warnings (Ukrainian). Answer only with the tool."""


def draft_dish(user, *, name, category, output_g, diets, wishes=""):
    from apps.catalog.models import Diet

    products = list(Product.objects.filter(is_active=True).order_by("name").values_list("pk", "name"))
    diet_rows = Diet.objects.filter(pk__in=diets).values_list("code", "name")
    text = (
        f"Страва: {name}\nКатегорія: {category or '—'}\nВихід на порцію: {output_g or 'на ваш розсуд'} г\n"
        f"Дієти: {'; '.join(f'{c} — {n}' for c, n in diet_rows) or '—'}\nПобажання: {wishes or '—'}\n\n"
        "Доступні продукти (id\tназва):\n" + "\n".join(f"{pk}\t{n}" for pk, n in products)
    )
    data = client.extract(
        user=user, feature="dish_draft", tool_name="draft_card", tool_schema=DISH_SCHEMA,
        system_prompt=DISH_PROMPT, max_tokens=2500, content_blocks=[{"type": "text", "text": text}],
        request_meta={"dish": name[:100], "diets": len(diets)},
    )
    known = dict(products)
    rows, dropped = [], []
    for ing in data.get("ingredients", []):
        pid = ing.get("product_id")
        try:
            brutto, netto = float(ing.get("brutto_g")), float(ing.get("netto_g"))
        except (TypeError, ValueError):
            continue
        if pid not in known or brutto <= 0 or netto < 0 or brutto > 5000:
            dropped.append(str(pid))
            continue
        if any(r["product_id"] == pid for r in rows):
            continue
        rows.append({"product_id": pid, "brutto_g": round(brutto, 1), "netto_g": round(netto, 1)})
    warnings = [str(w)[:300] for w in data.get("warnings", [])][:8]
    if dropped:
        warnings.append("ШІ вказав продукти, яких немає в довіднику — їх відкинуто.")
    out = data.get("output_g")
    return {
        "ingredients": rows,
        "output_g": round(float(out), 1) if isinstance(out, (int, float)) and 0 < out < 3000 else None,
        "technology": str(data.get("technology", ""))[:3000],
        "missing_products": [
            {"name": str(m.get("name", ""))[:150], "reason": str(m.get("reason", ""))[:200]}
            for m in data.get("missing_products", []) if m.get("name")
        ][:10],
        "warnings": warnings,
    }


# ============================================================ AI-06 product estimates
PRODUCTS_SCHEMA = {
    "type": "object",
    "properties": {
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "input_name": {"type": "string"},
                    "name": {"type": "string", "description": "нормалізована назва українською"},
                    "category": {"type": ["string", "null"]},
                    "protein": {"type": "number"}, "fat": {"type": "number"},
                    "carbs": {"type": "number"}, "kcal": {"type": "number"},
                    "waste_pct": {"type": ["number", "null"], "description": "типові відходи при холодній обробці, %"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "note": {"type": "string"},
                },
                "required": ["input_name", "name", "protein", "fat", "carbs", "kcal", "confidence", "note"],
            },
        }
    },
    "required": ["products"],
}

PRODUCTS_PROMPT = """For each food product name, give its typical chemical composition per 100 g of edible part
as in standard reference tables used in Ukraine (e.g. Скурихін «Хімічний склад харчових продуктів»):
protein, fat, carbohydrates (g) and energy (kcal). Also the typical waste % at cold processing, if relevant.
Pick category from the given list when one fits, else null. Normalise the name in Ukrainian.
If a name is ambiguous (fat content, grade, raw/cooked), choose the most common hospital-catering variant
and say which one in note (Ukrainian); lower confidence when unsure. Never give brand-specific values.
Answer only with the tool."""


def estimate_products(user, names):
    from apps.catalog.audit import normalize
    from apps.catalog.models import ProductCategory

    names = [n.strip()[:150] for n in names if n.strip()][:15]
    if not names:
        raise ValueError("Вкажіть хоча б одну назву продукту.")
    categories = list(ProductCategory.objects.values_list("name", flat=True))
    text = "Категорії: " + "; ".join(categories) + "\n\nПродукти:\n" + "\n".join(names)
    data = client.extract(
        user=user, feature="product_estimate", tool_name="estimate", tool_schema=PRODUCTS_SCHEMA,
        system_prompt=PRODUCTS_PROMPT, max_tokens=2500, content_blocks=[{"type": "text", "text": text}],
        request_meta={"names": len(names)},
    )
    existing = {normalize(n): n for n in Product.objects.values_list("name", flat=True)}
    rows = []
    for p in data.get("products", [])[:15]:
        try:
            vals = {k: round(float(p.get(k)), 2) for k in ("protein", "fat", "carbs", "kcal")}
        except (TypeError, ValueError):
            continue
        if any(v < 0 or v > 900 for v in vals.values()):
            continue
        name = str(p.get("name") or p.get("input_name") or "")[:150].strip()
        if not name:
            continue
        atwater = 4 * vals["protein"] + 9 * vals["fat"] + 4 * vals["carbs"]
        waste = p.get("waste_pct")
        rows.append({
            "input_name": str(p.get("input_name", ""))[:150], "name": name,
            "category": p.get("category") if p.get("category") in categories else "",
            **vals,
            "waste_pct": round(float(waste), 1) if isinstance(waste, (int, float)) and 0 <= waste < 100 else None,
            "confidence": p.get("confidence") if p.get("confidence") in ("high", "medium", "low") else "low",
            "note": str(p.get("note", ""))[:300],
            "kcal_mismatch": abs(vals["kcal"] - atwater) > max(10, 0.1 * max(vals["kcal"], atwater)),
            "exists": existing.get(normalize(name), ""),
        })
    return {"products": rows, "categories": categories}


# ============================================================ AI-10 technology for existing cards
TECHNOLOGY_SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dish_id": {"type": "integer"},
                    "technology": {"type": "string", "description": "3–6 речень українською"},
                    "warnings": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["dish_id", "technology", "warnings"],
            },
        }
    },
    "required": ["cards"],
}

TECHNOLOGY_PROMPT = """You write the «технологія приготування» section for EXISTING technological cards
(картки-розкладки) of a Ukrainian hospital kitchen (Наказ МОЗ №931, typical Ukrainian catering recipe collections).
For each card you get its name, category, portion output, diets and ingredients with netto grams per portion.
Write concise preparation steps in Ukrainian (3–6 sentences): cold processing, cooking method, portioning,
serving temperature. Use ONLY the listed ingredients — never add others and never change quantities.
Do not repeat the gram amounts. Respect the diets: ЩД/№1а — no frying, steamed or boiled, wiped or chopped
as needed; №9 — no added sugar; НКД — lean, no frying; НБД — as the card says. If the card looks inconsistent
(e.g. a porridge without water, output far from the ingredients), still write the technology and add a short
warning in Ukrainian. Return one entry per given dish_id. Answer only with the tool."""


def draft_technology(user, dishes):
    """dishes: Dish objects with ingredients__product and diets prefetched. Returns {dish_id: {technology, warnings}}."""
    blocks = []
    for d in dishes:
        ings = "; ".join(f"{i.product.name} {i.netto_g:g} г" for i in d.ingredients.all())
        diets = ", ".join(f"{x.code} ({x.name})" for x in d.diets.all()) or "—"
        blocks.append(f"dish_id: {d.pk}\nНазва: {d.name}\nКатегорія: {d.category or '—'}\n"
                      f"Вихід: {f'{d.output_g:g} г' if d.output_g else '—'}\nДієти: {diets}\nСклад (нетто): {ings or '—'}")
    data = client.extract(
        user=user, feature="dish_technology", tool_name="write_technology", tool_schema=TECHNOLOGY_SCHEMA,
        system_prompt=TECHNOLOGY_PROMPT, max_tokens=4000,
        content_blocks=[{"type": "text", "text": "\n\n".join(blocks)}],
        request_meta={"dishes": len(blocks)},
    )
    asked = {d.pk for d in dishes}
    out = {}
    for c in data.get("cards", []):
        pid, text = c.get("dish_id"), str(c.get("technology", "")).strip()
        if pid not in asked or pid in out or not text:      # unknown ids, repeats and blanks are dropped
            continue
        out[pid] = {"technology": text[:1500],
                    "warnings": [str(w)[:300] for w in c.get("warnings", []) if str(w).strip()][:3]}
    return out
