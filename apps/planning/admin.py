from django.contrib import admin

from .models import DailyPlan, DailyPlanEntry


class EntryInline(admin.TabularInline):
    model = DailyPlanEntry
    extra = 0


@admin.register(DailyPlan)
class DailyPlanAdmin(admin.ModelAdmin):
    list_display = ["service_date", "approved_at"]
    inlines = [EntryInline]

    def has_change_permission(self, request, obj=None):
        return False
