from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class HarchUserAdmin(UserAdmin):
    list_display = ["username", "last_name", "first_name", "role", "department", "is_active"]
    list_filter = ["role", "department", "is_active"]
    fieldsets = UserAdmin.fieldsets + (
        ("Роль у системі", {"fields": ("role", "department", "phone", "telegram_chat_id")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Роль у системі", {"fields": ("role", "department")}),
    )
