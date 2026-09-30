from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string

from apps.accounts.models import Role

from . import health
from .navigation import PLACEHOLDERS, roles_for


def robots_txt(request):
    """S6-04: nothing here is for search engines (the X-Robots-Tag header says the same)."""
    return HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")


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
        "sees_store": request.user.has_role(*roles_for("store")),
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


def _help_context(role):
    if role not in Role.values:
        raise Http404
    return {
        "role": role,
        "role_label": Role(role).label,
        "roles": Role.choices,
        "hospital": settings.HOSPITAL_NAME,
        "order_deadline": settings.ORDER_DEADLINE,
        "content": f"help/roles/{role}.html",
    }


@login_required
def help_page(request, role=None):
    """S5-04: «що я роблю щодня в системі». Own role by default; every role's page is readable by all."""
    return render(request, "help/index.html", _help_context(role or request.user.role))


@login_required
def help_kontur(request):
    """The whole day on diagrams: who submits, who approves or returns. Built by docs/kontur/build.py."""
    return render(request, "help/kontur.html", {"hospital": settings.HOSPITAL_NAME})


@login_required
def help_pdf(request, role):
    from weasyprint import HTML

    html = render_to_string("help/print.html", _help_context(role), request=request)
    response = HttpResponse(HTML(string=html).write_pdf(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="instrukciya_{role}.pdf"'
    return response
