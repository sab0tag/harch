from django.contrib import admin

from .models import Notification, Task, TaskEvent


class TaskEventInline(admin.TabularInline):
    model = TaskEvent
    extra = 0
    can_delete = False
    readonly_fields = ["kind", "actor", "from_status", "to_status", "comment", "created_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    """Read-only: status changes go through the workflow services, never through admin."""

    list_display = ["id", "type", "service_date", "department", "status", "deadline", "reject_count"]
    list_filter = ["type", "status", "service_date"]
    inlines = [TaskEventInline]

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["user", "title", "created_at", "read_at"]
    list_filter = ["channel"]
