from django.shortcuts import render
from django.views import View

from apps.accounts.access import RoleRequiredMixin
from apps.core.navigation import roles_for

from .models import Department


class DepartmentListView(RoleRequiredMixin, View):
    allowed_roles = roles_for("departments")

    def get(self, request):
        departments = Department.objects.select_related("default_diet").order_by("sort_order", "name")
        return render(request, "orgstructure/department_list.html", {"departments": departments})
