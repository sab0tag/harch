from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import render

from . import health
from .navigation import PLACEHOLDERS


def healthz(request):
    db = health.check_db()
    status = 200 if db == health.OK else 503
    return JsonResponse({"status": "ok" if status == 200 else "degraded", "db": db}, status=status)


def healthz_full(request):
    healthy, report = health.full_report()
    return JsonResponse(report, status=200 if healthy else 503)


@login_required
def home(request):
    from apps.workflow import services as wf

    return render(request, "core/home.html", {
        "todo_count": wf.todo_for(request.user).count(),
        "review_count": wf.review_for(request.user).count(),
        "overdue_count": wf.todo_for(request.user).overdue().count(),
    })


@login_required
def section(request, slug):
    """Placeholder for screens planned in later sprints. Enforces the nav item's roles."""
    item = PLACEHOLDERS.get(slug)
    if item is None:
        raise Http404
    if not request.user.has_role(*item.roles):
        raise PermissionDenied
    return render(request, "core/placeholder.html", {"item": item})
