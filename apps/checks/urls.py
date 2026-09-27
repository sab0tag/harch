from django.urls import path

from . import views

app_name = "checks"
urlpatterns = [
    path("", views.RunListView.as_view(), name="list"),
    path("<int:pk>/", views.RunDetailView.as_view(), name="run"),
]
