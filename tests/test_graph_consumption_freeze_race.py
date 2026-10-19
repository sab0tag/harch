"""PostgreSQL regression for concurrent snapshot freezing (invariant 5)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace

import pytest
from django.db import close_old_connections, connection

from apps.catalog.models import Product
from apps.consumption.models import ConsumptionSnapshotLine
from apps.consumption.services import AlreadyFrozen, freeze
from apps.planning.models import DailyPlan


@pytest.mark.django_db(transaction=True)
def test_concurrent_freeze_creates_one_snapshot(monkeypatch):
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row-level locking")

    from apps.consumption import services

    plan = DailyPlan.objects.create(service_date=date(2031, 1, 1))
    product = Product.objects.create(
        name="Freeze concurrency test product",
        protein=Decimal("1"), fat=Decimal("1"),
        carbs=Decimal("1"), kcal=Decimal("17"),
    )
    line = SimpleNamespace(
        diet_code="1", menu_number=1, meal_slot="breakfast",
        dish_card_no=1, dish_name="Test dish", dish_output_g=Decimal("100"),
        product_id=product.pk, product_name=product.name, portions=1,
        brutto_pp=Decimal("100"), netto_pp=Decimal("90"),
        brutto_total=Decimal("100"), netto_total=Decimal("90"),
        kcal_total=Decimal("17"),
    )
    monkeypatch.setattr(services, "compute_lines", lambda entries: [line])
    barrier = Barrier(2)

    def attempt(_):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                freeze(DailyPlan(pk=plan.pk))
                return "frozen"
            except AlreadyFrozen:
                return "already_frozen"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))

    assert sorted(results) == ["already_frozen", "frozen"]
    assert ConsumptionSnapshotLine.objects.filter(plan=plan).count() == 1
