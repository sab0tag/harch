"""Clean up after the removed weekly `run_checks` (apps/checks, merged into the nightly reference audit).

Dev databases that ran it have checks_* tables and `catalog_review` tasks. A task type missing from
the registry breaks every task list, so those tasks go; so do the orphan tables. No-op on a fresh
database (the app was never deployed).
"""
from django.db import migrations


def drop_review_tasks(apps, schema_editor):
    Task = apps.get_model("workflow", "Task")
    Task.objects.filter(type="catalog_review").delete()          # events and notifications cascade
    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.filter(app_label="checks").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("workflow", "0002_notification_url"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        # checks_checkrun.task_id points at workflow_task, so the tables go before the tasks.
        migrations.RunSQL(
            "DROP TABLE IF EXISTS checks_finding; DROP TABLE IF EXISTS checks_checkrun; "
            "DELETE FROM django_migrations WHERE app = 'checks';",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunPython(drop_review_tasks, migrations.RunPython.noop),
    ]
