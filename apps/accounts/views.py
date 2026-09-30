from django.contrib import messages
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.urls import reverse_lazy


class ChangePasswordView(PasswordChangeView):
    """S6-05: own password — voluntarily from the user menu, or forced after a temporary one."""

    template_name = "registration/password_change.html"
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        response = super().form_valid(form)
        user = form.user
        if user.must_change_password:
            user.must_change_password = False
            user.save(update_fields=["must_change_password"])
        messages.success(self.request, "Пароль змінено.")
        return response


class SignInView(LoginView):
    """Login that never returns to a background endpoint (the bell's count or list): someone who signs in
    after a lost session lands on a page, not on a bare number."""

    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_redirect_url(self):
        url = super().get_redirect_url()
        return "" if url.startswith("/tasks/notifications/") else url
