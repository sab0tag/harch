from django.conf import settings
from django.urls import reverse

from .navigation import visible_groups


def site(request):
    return {"HOSPITAL_NAME": settings.HOSPITAL_NAME}


def _href(item):
    if item.url_name:
        return reverse(item.url_name)
    if item.slug:
        return reverse("section", args=[item.slug])
    return item.url


def navigation(request):
    path = request.path
    groups = []
    for group in visible_groups(request.user):
        links = []
        for item in group.items:
            href = _href(item)
            active = path == href if href == "/" else path.startswith(href)
            links.append({"item": item, "href": href, "active": active})
        groups.append({"label": group.label, "links": links})
    return {"nav_groups": groups}
