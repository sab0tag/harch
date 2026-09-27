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
