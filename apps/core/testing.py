"""Test helpers shared across apps (not a test module)."""
from datetime import datetime
from zoneinfo import ZoneInfo

import time_machine


def on_the_day():
    """Evening of 1 October, the tests' service day: the kitchen fact may only be submitted on its day."""
    return time_machine.travel(datetime(2026, 10, 1, 20, 0, tzinfo=ZoneInfo("Europe/Kyiv")), tick=True)
