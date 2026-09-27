from django.urls import path

from . import views

app_name = "assist"
urlpatterns = [
    path("", views.AIStatusView.as_view(), name="status"),
]
