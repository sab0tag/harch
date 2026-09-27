from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import Http404, JsonResponse
from django.shortcuts import render

from .navigation import PLACEHOLDERS


def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        db = "ok"
    except Exception:  # noqa: BLE001 — healthcheck must never raise
        db = "error"
    status = 200 if db == "ok" else 503
    return JsonResponse({"status": "ok" if status == 200 else "degraded", "db": db}, status=status)


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
