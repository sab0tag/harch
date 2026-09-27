from decimal import Decimal

from django import template
from django.template.defaultfilters import floatformat

register = template.Library()

STATUS_CLASS = {"ok": "success", "warn": "warning", "bad": "danger"}


@register.filter
def num(value, places=1):
    """Localised number with thousands grouping; trailing zeros dropped."""
    if value in (None, ""):
        return "—"
    return floatformat(value, f"-{places}g")


@register.filter
def status_color(status):
    return STATUS_CLASS.get(status, "secondary")


@register.filter
def pct(value):
    if value in (None, ""):
        return "—"
    return floatformat(Decimal(value), "-1") + "%"


@register.filter
def role_label(role):
    from apps.accounts.models import Role

    return dict(Role.choices).get(role, role)


@register.filter
def ago(value):
    """«щойно» for under a minute, otherwise «N хвилин тому»."""
    from django.utils import timezone
    from django.utils.timesince import timesince

    if not value:
        return ""
    if (timezone.now() - value).total_seconds() < 60:
        return "щойно"
    return f"{timesince(value).split(',')[0]} тому"


@register.filter
def get(mapping, key):
    """Dict lookup in templates: {{ d|get:key }}."""
    try:
        return mapping.get(key, "")
    except AttributeError:
        return ""


@register.filter
def kg(grams):
    """Grams (Decimal) → kilograms with 3 decimals, localised."""
    if grams in (None, ""):
        return "—"
    return floatformat(Decimal(grams) / 1000, "3g")
