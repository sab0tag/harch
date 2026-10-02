"""Create the day's order tasks (one per active department). Idempotent — safe to run often.

    python manage.py generate_daily_tasks                 # tomorrow … next working day (scheduled at 00:05):
                                                          #   on Friday — Saturday, Sunday and Monday
    python manage.py generate_daily_tasks --date 2026-10-01
    python manage.py generate_daily_tasks --days-ahead 3  # tomorrow .. +3
"""
from datetime import date as date_cls, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.orgstructure.models import Department
from apps.workflow.definitions import ORDER_SUBMISSION, next_workday, open_plan_if_ready
from apps.workflow.services import ensure_task


class Command(BaseCommand):
    help = "Create order_submission tasks for every active department."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Service date YYYY-MM-DD")
        parser.add_argument("--days-ahead", type=int, help="Create for tomorrow..today+N "
                                                           "(default: through the next working day)")

    def handle(self, *args, date=None, days_ahead=None, **options):
        if date:
            try:
                days = [date_cls.fromisoformat(date)]
            except ValueError as exc:
                raise CommandError(f"Bad --date: {date}") from exc
        else:
            today = timezone.localdate()
            if days_ahead is None:  # orders for a weekend are handed in on Friday, with Monday's
                days_ahead = (next_workday(today + timedelta(days=1)) - today).days
            if days_ahead < 1:
                raise CommandError("--days-ahead must be >= 1")
            days = [today + timedelta(days=i) for i in range(1, days_ahead + 1)]
        departments = list(Department.objects.filter(is_active=True).order_by("sort_order", "name"))
        for day in days:
            created = sum(ensure_task(ORDER_SUBMISSION, day, department=d,
                                      reason="Нічний планувальник: замовлення на наступний робочий день")[1]
                          for d in departments)
            open_plan_if_ready(day)      # safety net: a day whose last missing department was switched off
            self.stdout.write(f"{day:%Y-%m-%d}: created {created}, departments {len(departments)}")
