"""Cross-cutting HTTP behaviour for every response."""


class NoIndexMiddleware:
    """S6-04: an internal hospital system stays out of search engines, on every page and file."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response
