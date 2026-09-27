"""Error reporting to Sentry (S5-05). Off unless SENTRY_DSN is set.

Staff are the only people with personal data here (docs/06_deploy.md «Дані і приватність»), so
Sentry gets no user identity, IPs, cookies or request bodies — stack traces and URLs only.
"""


def init_sentry(dsn, environment, release=None):
    if not dsn:
        return False
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release or None,
        integrations=[DjangoIntegration()],
        send_default_pii=False,
        max_request_body_size="never",
        traces_sample_rate=0,  # errors only; the free plan's performance quota is not needed
    )
    return True
