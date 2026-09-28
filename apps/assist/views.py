from django.contrib import messages
from django.shortcuts import redirect, render
from django.views import View

from apps.accounts.access import RoleRequiredMixin
from apps.core.navigation import roles_for

from . import client
from .models import AIRequestLog


class AIStatusView(RoleRequiredMixin, View):
    """Admin: is AI on, is a key configured (never shown), usage, recent calls, connection check."""

    allowed_roles = roles_for("ai")

    def get(self, request):
        return render(request, "assist/status.html", {
            "st": client.status(),
            "logs": AIRequestLog.objects.select_related("user")[:50],
        })

    def post(self, request):
        try:
            checked = client.check_connection(request.user)
        except client.AIUnavailable:
            messages.error(request, "ШІ вимкнено або ключ не вказано — дивіться інструкцію нижче.")
        except (client.AILimitExceeded, client.AIError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "З'єднання з Anthropic працює: " + ", ".join(checked) + ".")
        return redirect("assist:status")
