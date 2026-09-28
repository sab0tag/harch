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
