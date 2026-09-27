from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Menu, MenuItem


class MenuItemInline(admin.TabularInline):
    model = MenuItem
    extra = 0
    autocomplete_fields = ["dish"]


@admin.register(Menu)
class MenuAdmin(SimpleHistoryAdmin):
    list_display = ["number", "diet", "title", "verified", "is_active"]
    list_filter = ["diet", "verified", "is_active"]
    search_fields = ["number", "title"]
    inlines = [MenuItemInline]
