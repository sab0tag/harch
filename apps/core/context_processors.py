from django.conf import settings
from django.urls import reverse

from .navigation import visible_groups


def site(request):
    return {"HOSPITAL_NAME": settings.HOSPITAL_NAME}


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
