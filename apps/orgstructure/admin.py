from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Department


@admin.register(Department)
class DepartmentAdmin(SimpleHistoryAdmin):
    list_display = ["name", "patient_category", "default_diet", "beds", "is_active", "sort_order"]
    list_filter = ["patient_category", "is_active"]
    search_fields = ["name"]
