from django.conf import settings
from django.utils import timezone
from django.urls import reverse

from .navigation import visible_groups


FIRST_YEAR = 2026


def demo_nurses():
    """Training copy sign-in page: every department's head-nurse login (called only if the page shows it)."""
    from apps.accounts.models import Role, User

    return User.objects.filter(role=Role.DEPT_NURSE, is_active=True, username__startswith="nurse-") \
        .select_related("department").order_by("department__sort_order", "department__name")


def site(request):
    year = timezone.localdate().year
    return {"HOSPITAL_NAME": settings.HOSPITAL_NAME, "HOSPITAL_CITY": settings.HOSPITAL_CITY,
            "DEMO_MODE": settings.DEMO_MODE, "DEMO_NURSES": demo_nurses if settings.DEMO_MODE else (), "FOOTER_YEARS": f"{FIRST_YEAR}–{year}" if year > FIRST_YEAR else str(FIRST_YEAR)}


def _href(item):
    if item.url_name:
        return reverse(item.url_name)
    if item.is_placeholder:
        return reverse("section", args=[item.key])
    return item.url


def navigation(request):
    """Sidebar links; the single active one is the longest href that prefixes the path."""
    path = request.path
    groups = []
    for group in visible_groups(request.user):
        links = [{"item": item, "href": _href(item), "active": False} for item in group.items]
        groups.append({"label": group.label, "links": links})
    matches = [
        link for g in groups for link in g["links"]
        if (path == link["href"] if link["href"] == "/" else path.startswith(link["href"]))
    ]
    if matches:
        max(matches, key=lambda link: len(link["href"]))["active"] = True
    return {"nav_groups": groups}
