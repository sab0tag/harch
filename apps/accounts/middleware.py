import time
from urllib.parse import quote, urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import HttpResponse
from django.shortcuts import redirect, resolve_url
from django.urls import NoReverseMatch, reverse

LAST_ACTIVITY = "last_activity"


class IdleLogoutMiddleware:
    """S6-02: department computers are shared — sign out after SESSION_IDLE_MINUTES without activity.

    Background polling (the notification badge, every 60 s) is not activity; after the timeout that
    same poll sends the whole page to the login screen, so nobody's data stays open on the monitor.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def is_passive(request):
        try:
            return request.path == reverse("workflow:notification_count")
        except NoReverseMatch:  # a URLconf without the badge (some tests)
            return False

    def __call__(self, request):
        if request.user.is_authenticated:
            now = int(time.time())
            last = request.session.get(LAST_ACTIVITY)
            if last is not None and now - last > settings.SESSION_IDLE_MINUTES * 60:
                return self.sign_out(request)
            if not self.is_passive(request):
                request.session[LAST_ACTIVITY] = now
        return self.get_response(request)

    def sign_out(self, request):
        is_htmx = request.headers.get("HX-Request") == "true"
        current = urlsplit(request.headers.get("HX-Current-URL", "")) if is_htmx else None
        back = (current.path + (f"?{current.query}" if current.query else "")) if current else request.get_full_path()
        logout(request)
        messages.info(request, f"Вихід виконано автоматично: {settings.SESSION_IDLE_MINUTES} хв без дій. Увійдіть ще раз.")
        url = f"{resolve_url(settings.LOGIN_URL)}?next={quote(back or '/')}"
        if is_htmx:
            response = HttpResponse(status=204)
            response["HX-Redirect"] = url
            return response
        return redirect(url)
