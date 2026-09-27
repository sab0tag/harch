from django.contrib import admin

from .models import DailyOrder, DailyOrderLine


class LineInline(admin.TabularInline):
    model = DailyOrderLine
    extra = 0


@admin.register(DailyOrder)
class DailyOrderAdmin(admin.ModelAdmin):
    list_display = ["service_date", "department", "no_patients", "updated_by", "updated_at"]
    list_filter = ["service_date", "department"]
    inlines = [LineInline]

    def has_change_permission(self, request, obj=None):
        return False  # changes go through the order screen and the workflow
