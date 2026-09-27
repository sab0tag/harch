"""Role-based access helpers. Views stay thin: declare roles, never check inline."""
from functools import wraps

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


class RoleRequiredMixin(LoginRequiredMixin):
    """Anonymous -> login redirect; authenticated with wrong role -> 403.

    Admin role always passes. Set `allowed_roles` on the view.
    """

    allowed_roles: tuple[str, ...] = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.has_role(*self.allowed_roles):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


def role_required(*roles):
    """Function-view counterpart of RoleRequiredMixin."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not request.user.has_role(*roles):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
