from django.urls import path

from . import views

app_name = "orgstructure"

urlpatterns = [
    path("departments/", views.DepartmentListView.as_view(), name="department_list"),
]
