"""AI-10: «Технології з ШІ» — the model proposes preparation steps for cards that have none.

Proposals live in an AIDraft (kind=technology) until a dietician accepts them one by one or in a
group; only then Dish.technology is written (with history). Nothing is written automatically.
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.assist.models import AIDraft

from .models import Dish

BATCH = 10          # cards per model call


def pending_ids():
    """Dishes already waiting in an undecided proposal — not proposed twice."""
    ids = set()
    for draft in AIDraft.objects.filter(kind=AIDraft.Kind.TECHNOLOGY, status=AIDraft.Status.READY):
        decided = draft.result.get("decisions", {})
        ids |= {int(k) for k in draft.result.get("items", {}) if k not in decided}
    return ids


def candidates():
    return (Dish.objects.filter(is_active=True, technology="").exclude(pk__in=pending_ids())
            .order_by("card_no"))


def generate(user, limit=BATCH):
    """One draft for up to `limit` cards, BATCH per model call. Raises the client's AI errors."""
    from apps.assist.features import draft_technology

    dishes = list(candidates().prefetch_related("ingredients__product", "diets")[:limit])
    if not dishes:
        return None
    items = {}
    for i in range(0, len(dishes), BATCH):
        items.update(draft_technology(user, dishes[i:i + BATCH]))
    if not items:
        raise ValidationError("ШІ не повернув жодної технології — спробуйте ще раз.")
    return AIDraft.objects.create(
        kind=AIDraft.Kind.TECHNOLOGY, user=user, input={"dish_ids": [d.pk for d in dishes]},
        result={"items": {str(k): v for k, v in items.items()}, "decisions": {}},
    )


def rows(draft):
    """[{dish, proposal, warnings, decision}] in card order; dishes deleted meanwhile are skipped."""
    items = draft.result.get("items", {})
    decisions = draft.result.get("decisions", {})
    dishes = Dish.objects.filter(pk__in=[int(k) for k in items]).prefetch_related("ingredients__product", "diets")
    return [{"dish": d, "proposal": items[str(d.pk)]["technology"], "warnings": items[str(d.pk)]["warnings"],
             "decision": decisions.get(str(d.pk))} for d in sorted(dishes, key=lambda d: d.card_no)]


@transaction.atomic
def decide(draft, user, accepted: dict, discarded: set):
    """accepted: {dish_id: text as edited by the person}; discarded: {dish_id}. Returns (written, skipped)."""
    draft = AIDraft.objects.select_for_update().get(pk=draft.pk)
    if draft.status != AIDraft.Status.READY:
        raise ValidationError("Цю пропозицію вже опрацьовано.")
    items, decisions = draft.result.get("items", {}), draft.result.setdefault("decisions", {})
    written, skipped = [], []
    for pk, text in accepted.items():
        key = str(pk)
        if key not in items or key in decisions:
            continue
        text = (text or "").strip()[:3000]
        dish = Dish.objects.select_for_update().filter(pk=pk).first()
        if dish is None or not text:
            continue
        if dish.technology.strip():          # someone filled it in meanwhile — never overwrite a person
            decisions[key] = "skipped"
            skipped.append(dish)
            continue
        dish.technology = text
        dish.verified = False
        dish._change_reason = f"Технологія: пропозиція ШІ, прийняв(ла) {user}"
        dish.save()
        decisions[key] = "accepted"
        written.append(dish)
    for pk in discarded:
        if str(pk) in items and str(pk) not in decisions:
            decisions[str(pk)] = "discarded"
    if len(decisions) >= len(items):
        draft.status = AIDraft.Status.ACCEPTED if "accepted" in decisions.values() else AIDraft.Status.DISCARDED
    draft.created_ids = sorted({*draft.created_ids, *(d.pk for d in written)})
    draft.save(update_fields=["result", "status", "created_ids", "updated_at"])
    return written, skipped
