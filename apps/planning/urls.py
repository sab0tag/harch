from django.urls import path

from . import views

app_name = "planning"

urlpatterns = [
    path("", views.PlanView.as_view(), name="today"),
    path("sign/", views.SignView.as_view(), name="sign"),
    path("<str:day>/", views.PlanView.as_view(), name="plan"),
    path("<str:day>/preview/", views.PlanPreviewView.as_view(), name="preview"),
]
