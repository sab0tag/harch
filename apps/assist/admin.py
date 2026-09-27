from django.contrib import admin

from .models import AIRequestLog


@admin.register(AIRequestLog)
class AIRequestLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "feature", "status", "user", "model", "input_tokens", "output_tokens", "duration_ms")
    list_filter = ("status", "feature", "model")
    readonly_fields = [f.name for f in AIRequestLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
