from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse




class ForcePasswordChangeMiddleware:
    """S6-05: a temporary password opens only the password page until the user sets their own."""

    ALLOWED = ("password_change", "logout")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and user.must_change_password and not self.allowed(request):
            if request.headers.get("HX-Request") == "true":
                response = HttpResponse(status=204)
                response["HX-Redirect"] = reverse("password_change")
                return response
            return redirect("password_change")
        return self.get_response(request)

    def allowed(self, request):
        if request.path.startswith(("/" + settings.STATIC_URL.lstrip("/"), "/healthz/", "/robots.txt")):
            return True
        try:
            return request.path in {reverse(name) for name in self.ALLOWED}
        except NoReverseMatch:  # a URLconf without these pages (some tests)
            return True
