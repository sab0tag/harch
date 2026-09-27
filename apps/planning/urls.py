from django.urls import path

from . import views

app_name = "planning"

urlpatterns = [
    path("", views.PlanView.as_view(), name="today"),
    path("<str:day>/", views.PlanView.as_view(), name="plan"),
    path("<str:day>/preview/", views.PlanPreviewView.as_view(), name="preview"),
]
