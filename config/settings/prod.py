from config.sentry import init_sentry

from .base import *  # noqa: F401,F403

DEBUG = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS]  # noqa: F405
# Behind Caddy: the client address is the right-most X-Forwarded-For entry (Caddy sets it), not REMOTE_ADDR.
AXES_IPWARE_META_PRECEDENCE_ORDER = ["HTTP_X_FORWARDED_FOR", "REMOTE_ADDR"]
AXES_IPWARE_PROXY_ORDER = "right-most"

# HSTS preload is a months-long commitment to the browser lists; not for a pilot domain.
SILENCED_SYSTEM_CHECKS = ["security.W021"]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

# Errors → Sentry when SENTRY_DSN is set (S5-05). Applies to web and scheduler (same settings).
init_sentry(env("SENTRY_DSN"), env("SENTRY_ENVIRONMENT"), env("SENTRY_RELEASE", default=""))  # noqa: F405
