from django.contrib import admin

from .models import CheckRun, Finding


class FindingInline(admin.TabularInline):
    model = Finding
    extra = 0
    fields = ("severity", "title", "status", "comment", "ai_suggestion")
    readonly_fields = fields


@admin.register(CheckRun)
class CheckRunAdmin(admin.ModelAdmin):
    list_display = ("run_date", "ai_status", "task")
    inlines = [FindingInline]
