"""The PR risk tier (.github/scripts/risk_tier.py) is decided by paths: what may auto-merge and what may not."""
import importlib.util
from pathlib import Path

import pytest
from django.conf import settings

spec = importlib.util.spec_from_file_location(
    "risk_tier", Path(settings.BASE_DIR) / ".github" / "scripts" / "risk_tier.py")
risk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(risk)

REQ = "Django>=5.2,<5.3\npsycopg[binary]>=3.2\nanthropic>=1.8,<2\n"


@pytest.mark.parametrize("paths", [
    ["docs/05_sprints.md"],
    [".claude/commands/steward.md", "README.md"],
    ["apps/catalog/tests/test_audit.py", "conftest.py"],
    [],
])
def test_low(paths):
    assert risk.classify(paths) == "low"


@pytest.mark.parametrize("paths", [
    ["apps/consumption/calc.py"],
    ["docs/05_sprints.md", "apps/workflow/services.py"],          # one app file is enough
    ["apps/catalog/migrations/0004_x.py"],
    ["templates/reports/index.html"],
    ["config/settings/prod.py"],
    [".github/workflows/tests.yml"],                              # CI itself is not low risk
    ["deploy/update.sh"],
    ["apps/catalog/testsuite.py"],                                # not a tests/ package
])
def test_high(paths):
    assert risk.classify(paths) == "high"


def test_requirement_bounds_of_same_packages_are_low():
    new = REQ.replace("Django>=5.2,<5.3", "Django>=5.2.9,<5.3")
    assert risk.classify(["requirements.txt"], {"requirements.txt": (REQ, new)}) == "low"


def test_new_or_removed_package_is_high():
    added = REQ + "requests>=2.32\n"
    removed = REQ.replace("anthropic>=1.8,<2\n", "")
    assert risk.classify(["requirements.txt"], {"requirements.txt": (REQ, added)}) == "high"
    assert risk.classify(["requirements.txt"], {"requirements.txt": (REQ, removed)}) == "high"
    assert risk.classify(["requirements.txt"], {"requirements.txt": (REQ, None)}) == "high"   # deleted file


def test_package_names_ignore_extras_comments_and_includes():
    assert risk.package_names("# c\n-r requirements.txt\npsycopg[binary]>=3.2  # db\nfactory_boy>=3.3\n") == \
        {"psycopg", "factory-boy"}
