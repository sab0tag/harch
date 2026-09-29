"""Play the last N days through the whole chain so every role has something to look at.

    python manage.py demo_days --days 10
    python manage.py demo_days --days 7 --live   # the training copy: open work for every role today

Each day goes orders → plan → kitchen fact → write-off through the same workflow services the UI
uses (maker ≠ checker, validations, snapshots). The last day stops at the write-off, so the
accountant finds an open task. Days that already have an approved plan are skipped. Products
without a price get an indicative demo price. Dev and training copy only (DEBUG or DEMO_MODE).

--live also plays today up to the kitchen (the chef enters the fact) and submits tomorrow's orders
for every other department; the rest — Терапевтичне (demo `nurse`) included — wait for their head
nurses, so a class of nurses has orders to enter and the dietician has orders to review.
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.catalog.models import Product
from apps.consumption.services import ensure_actuals, save_actuals, write_off_task
from apps.orders.services import open_order, orderable_diets, submit_order
from apps.orgstructure.models import Department
from apps.planning.models import DailyPlan
from apps.planning.services import open_plan
from apps.workflow import services as wf
from apps.workflow.definitions import COOKING, MENU_PLANNING, ORDER_SUBMISSION
from apps.workflow.models import Task, TaskStatus

# Indicative UAH/kg by a word in the product name; first match wins.
DEMO_PRICES = [
    ("вода", 0),
    ("яловичин", 320), ("свинин", 260), ("телятин", 340), ("курк", 150), ("кур", 150), ("індич", 230),
    ("печінк", 120), ("судак", 280), ("хек", 170), ("минта", 150), ("риб", 180), ("масло вершк", 380),
    ("сир твердий", 360), ("сир", 190), ("сметан", 120), ("молок", 40), ("кефір", 45), ("йогурт", 70),
    ("яйц", 90), ("олія", 75), ("цукор", 32), ("борошн", 22), ("хліб", 35), ("батон", 45),
    ("рис", 55), ("гречк", 50), ("вівсян", 35), ("пшон", 30), ("манн", 30), ("перлов", 25), ("крупа", 35),
    ("макарон", 40), ("картопл", 18), ("буряк", 16), ("морква", 18), ("капуст", 15), ("цибул", 17),
    ("огір", 60), ("помідор", 70), ("томат", 70), ("кабач", 35), ("гарбуз", 20), ("яблук", 30),
    ("родзинк", 150), ("сухофрукт", 120), ("чай", 400), ("какао", 350), ("сіль", 12), ("горох", 40),
    ("квасол", 60), ("сік", 45), ("кисіль", 110),
]
DEFAULT_PRICE = 60
REASONS = ["Заміна: недовага постачання", "Брак при прийманні", "Додаткова порція для відділення",
           "Втрати при обробці вищі за норму"]


def demo_price(name):
    low = name.lower()
    return Decimal(next((p for word, p in DEMO_PRICES if word in low), DEFAULT_PRICE))


class Command(BaseCommand):
    help = "Play the last N days through orders → plan → kitchen → write-off (demo data). Dev/training copy only."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=10, help="How many days back from yesterday (1–31)")
        parser.add_argument("--live", action="store_true", help="Also today (to the kitchen) and tomorrow (orders)")

    def handle(self, *args, days=10, live=False, **options):
        if not (settings.DEBUG or settings.DEMO_MODE):
            raise CommandError("demo_days runs only in dev (DEBUG) or on the training copy (DEMO_MODE)")
        if not 1 <= days <= 31:
            raise CommandError("--days must be 1..31")
        call_command("create_demo_users", stdout=self.stdout)
        self.people = {u.username: u for u in User.objects.filter(
            username__in=["admin", "dietician", "chef", "nurse", "accountant"])}
        self.head_nurse = {}                             # department → a nurse who submits its orders
        for u in User.objects.filter(role=Role.DEPT_NURSE, is_active=True).order_by("username"):
            self.head_nurse.setdefault(u.department_id, u)

        priced = 0
        for product in Product.objects.filter(price_per_kg__isnull=True):
            product.price_per_kg = demo_price(product.name)
            product.save()
            priced += 1
        if priced:
            self.stdout.write(f"demo prices set for {priced} products")

        today = timezone.localdate()
        span = [today - timedelta(days=i) for i in range(days, 0, -1)]
        for day in span:
            if DailyPlan.objects.filter(service_date=day, approved_at__isnull=False).exists():
                self.stdout.write(f"{day}: already played, skipped")
                continue
            self.play_day(day, stop="closed" if day != span[-1] else "write_off")
        if live:
            self.play_day(today, stop="cooking")
            self.play_day(today + timedelta(days=1), stop="orders")

    # ------------------------------------------------------------------ one day
    def play_day(self, day, stop):
        """stop: orders (submitted, not reviewed) · cooking (plan approved) · write_off (open) · closed."""
        rng = random.Random(day.toordinal())
        dietician, chef, accountant = self.people["dietician"], self.people["chef"], self.people["accountant"]

        diets = {d.code: d for d in orderable_diets()}
        fallback = diets.get("ОВД") or next(iter(diets.values()))
        portions_total = 0
        for i, dept in enumerate(Department.objects.filter(is_active=True).order_by("sort_order", "name")):
            main = dept.default_diet if dept.default_diet and dept.default_diet.code in diets else fallback
            base = random.Random(dept.pk).randint(6, 30)
            portions = {main.pk: max(1, round(base * rng.uniform(0.85, 1.15)))}
            if rng.random() < 0.35:                      # a few patients on another diet
                other = rng.choice(list(diets.values()))
                portions[other.pk] = portions.get(other.pk, 0) + rng.randint(1, 4)
            if stop == "orders" and (i % 2 == 0 or dept.pk == self.people["nurse"].department_id):
                open_order(dept, day)                    # left for this department's head nurse
                continue
            submit_order(open_order(dept, day), self.head_nurse.get(dept.pk, self.people["admin"]), portions)
            portions_total += sum(portions.values())
        if stop == "orders":
            waiting = Task.objects.filter(type=ORDER_SUBMISSION, service_date=day, status=TaskStatus.SUBMITTED).count()
            self.stdout.write(f"{day}: {waiting} orders submitted for the dietician, the rest wait for their nurses")
            return
        for task in Task.objects.filter(type=ORDER_SUBMISSION, service_date=day, status=TaskStatus.SUBMITTED):
            wf.approve(task, dietician)

        plan = open_plan(day)
        wf.submit(Task.objects.get(type=MENU_PLANNING, service_date=day), dietician)
        wf.approve(Task.objects.get(type=MENU_PLANNING, service_date=day), chef)
        plan.refresh_from_db()
        if stop == "cooking":
            self.stdout.write(f"{day}: {portions_total} portions, plan approved — the chef enters the fact")
            return

        # Kitchen: mostly as planned, small noise, one real deviation with a reason.
        ensure_actuals(plan)
        actuals = list(plan.actuals.order_by("product_name"))
        changes = {}
        for a in rng.sample(actuals, min(3, len(actuals))):
            changes[a.pk] = ((a.planned_brutto_g * Decimal(str(rng.uniform(0.96, 1.06)))).quantize(Decimal("0.01")), "")
        big = rng.choice(actuals)
        changes[big.pk] = ((big.planned_brutto_g * Decimal(str(rng.uniform(1.12, 1.25)))).quantize(Decimal("0.01")),
                           rng.choice(REASONS))
        save_actuals(plan, chef, changes)
        cooking = Task.objects.get(type=COOKING, service_date=day)
        wf.submit(cooking, chef)
        wf.approve(cooking, dietician)

        write_off = write_off_task(day)
        state = "write-off waiting for the accountant"
        if stop == "closed":
            wf.submit(write_off, accountant)
            wf.approve(write_off, dietician)
            state = "closed"
        self.stdout.write(f"{day}: {portions_total} portions, {len(actuals)} products — {state}")
