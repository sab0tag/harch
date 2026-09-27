from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("s/<slug:slug>/", views.section, name="section"),
    path("healthz/", views.healthz, name="healthz"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html", redirect_authenticated_user=True),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
