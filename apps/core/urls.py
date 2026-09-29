from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("s/<slug:slug>/", views.section, name="section"),
    path("help/", views.help_page, name="help"),
    path("help/kontur/", views.help_kontur, name="help_kontur"),
    path("help/<str:role>/", views.help_page, name="help_role"),
    path("help/<str:role>.pdf", views.help_pdf, name="help_pdf"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("healthz/", views.healthz, name="healthz"),
    path("healthz/full/", views.healthz_full, name="healthz_full"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html", redirect_authenticated_user=True),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
