"""Base settings shared by all environments."""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    HOSPITAL_NAME=(str, "Лікарня (пілот)"),
    HOSPITAL_FULL_NAME=(str, ""),
    HOSPITAL_EDRPOU=(str, ""),
    HOSPITAL_ADDRESS=(str, ""),
    APPROVER_TITLE=(str, "Директор"),
    ORDER_DEADLINE=(str, "16:00"),
    NOTIFY_EMAIL=(bool, False),
    AI_ENABLED=(bool, False),
    ANTHROPIC_MODEL=(str, "claude-sonnet-5"),
    ANTHROPIC_MODEL_LIGHT=(str, ""),
    AI_DAILY_REQUEST_LIMIT=(int, 200),
    AI_TIMEOUT_SECONDS=(int, 90),
    HEALTH_SCHEDULER_MAX_AGE_MINUTES=(int, 45),
    HEALTH_BACKUP_FILE=(str, ""),
    HEALTH_BACKUP_MAX_AGE_HOURS=(int, 26),
    SENTRY_DSN=(str, ""),
    SENTRY_ENVIRONMENT=(str, "production"),
)
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "simple_history",
    "apps.core",
    "apps.accounts",
    "apps.orgstructure",
    "apps.catalog",
    "apps.menus",
    "apps.consumption",
    "apps.workflow",
    "apps.orders",
    "apps.planning",
    "apps.assist",
    "apps.reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.accounts.middleware.ForcePasswordChangeMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site",
                "apps.core.context_processors.navigation",
                "apps.assist.context_processors.ai",
            ],
        },
    },
]

DATABASES = {"default": env.db("DATABASE_URL")}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "uk"
LANGUAGES = [("uk", "Українська")]
TIME_ZONE = "Europe/Kyiv"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

HOSPITAL_NAME = env("HOSPITAL_NAME")  # on screen: short name
# Printed forms (PDF, typical forms З-4а / З-13): official name from the ЄДР, code, address, who approves.
HOSPITAL_FULL_NAME = env("HOSPITAL_FULL_NAME") or HOSPITAL_NAME
HOSPITAL_EDRPOU = env("HOSPITAL_EDRPOU")
HOSPITAL_ADDRESS = env("HOSPITAL_ADDRESS")
APPROVER_TITLE = env("APPROVER_TITLE")
ORDER_DEADLINE = env("ORDER_DEADLINE")
NOTIFY_EMAIL = env("NOTIFY_EMAIL")

# AI (docs/07_ai.md). Only non-secret knobs live in settings; ANTHROPIC_API_KEY is read from the
# environment inside apps/assist/client.py and nowhere else.
AI_ENABLED = env("AI_ENABLED")
ANTHROPIC_MODEL = env("ANTHROPIC_MODEL")
ANTHROPIC_MODEL_LIGHT = env("ANTHROPIC_MODEL_LIGHT")  # cheaper model for text-only features; empty = main model
AI_DAILY_REQUEST_LIMIT = env("AI_DAILY_REQUEST_LIMIT")
AI_TIMEOUT_SECONDS = env("AI_TIMEOUT_SECONDS")
EMAIL_CONFIG = env.email_url("EMAIL_URL", default="consolemail://")
vars().update(EMAIL_CONFIG)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="harch@localhost")

# Monitoring (S5-05, docs/06_deploy.md «Мониторинг»). /healthz/full/ fails when the scheduler has not
# beaten for this long (check_deadlines runs every 15 min) or the last backup is older than this.
HEALTH_SCHEDULER_MAX_AGE_MINUTES = env("HEALTH_SCHEDULER_MAX_AGE_MINUTES")
HEALTH_BACKUP_FILE = env("HEALTH_BACKUP_FILE")  # empty = backups not monitored (dev)
HEALTH_BACKUP_MAX_AGE_HOURS = env("HEALTH_BACKUP_MAX_AGE_HOURS")
