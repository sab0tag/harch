from django.apps import AppConfig


class ChecksConfig(AppConfig):
    name = "apps.checks"
    verbose_name = "Перевірки довідників"

    def ready(self):
        from . import definitions  # noqa: F401 — registers the catalog_review task type
