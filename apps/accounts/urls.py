from django.urls import path

from . import views

urlpatterns = [
    path("password/", views.ChangePasswordView.as_view(), name="password_change"),
]
