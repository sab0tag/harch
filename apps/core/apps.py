from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "apps.core"
    verbose_name = "Ядро"

    def ready(self):
        # Django admin speaks as Харчоблок, not «Адміністрування Django» (templates/admin/base_site.html).
        from django.conf import settings
        from django.contrib import admin

        admin.site.site_header = "Харчоблок"
        admin.site.site_title = f"Харчоблок · {settings.HOSPITAL_NAME}"
        admin.site.index_title = "Довідники, користувачі та налаштування"
        admin.site.site_url = "/"

        # Login protection (django-axes) in Ukrainian; Django groups are unused — roles are User.role.
        from django.apps import apps
        from django.contrib.auth.models import Group

        if apps.is_installed("axes"):
            from axes.models import AccessAttempt, AccessFailureLog, AccessLog

            apps.get_app_config("axes").verbose_name = "Захист входу"
            for model, single, plural in ((AccessAttempt, "Блокування", "Блокування (невдалі спроби підряд)"),
                                          (AccessFailureLog, "Невдалий вхід", "Невдалі входи"),
                                          (AccessLog, "Вхід", "Входи")):
                model._meta.verbose_name, model._meta.verbose_name_plural = single, plural
        if admin.site.is_registered(Group):
            admin.site.unregister(Group)
