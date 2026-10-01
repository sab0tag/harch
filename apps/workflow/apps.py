from django.apps import AppConfig


class WorkflowConfig(AppConfig):
    name = "apps.workflow"
    verbose_name = "Задачі та сповіщення"

    def ready(self):
        from . import definitions, signals  # noqa: F401 — registers task types and department hooks
