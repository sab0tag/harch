"""Throwaway views used only to test access helpers."""
from django.http import HttpResponse
from django.urls import path
from django.views import View

from apps.accounts.access import RoleRequiredMixin, role_required
from apps.accounts.models import Role


class KitchenOnlyView(RoleRequiredMixin, View):
    allowed_roles = (Role.CHEF,)

    def get(self, request):
        return HttpResponse("ok")


@role_required(Role.ACCOUNTANT)
def accounting_only(request):
    return HttpResponse("ok")


urlpatterns = [
    path("kitchen/", KitchenOnlyView.as_view()),
    path("accounting/", accounting_only),
    path("login/", lambda r: HttpResponse("login"), name="login"),
]
