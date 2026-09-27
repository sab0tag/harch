import os
import subprocess
import sys

from django.conf import settings


def test_prod_settings_pass_deploy_checks():
    """S5-01: `check --deploy` on prod settings must report no warnings."""
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.prod",
        "DJANGO_SECRET_KEY": "x" * 60 + "prod-check-not-a-real-key",
        "DJANGO_ALLOWED_HOSTS": "harch.example.com",
        "DATABASE_URL": "sqlite:///:memory:",
    }
    result = subprocess.run(
        [sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
        cwd=settings.BASE_DIR, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no issues" in result.stdout
