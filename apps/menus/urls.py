from django.urls import path

from . import views

app_name = "menus"

urlpatterns = [
    path("", views.MenuListView.as_view(), name="list"),
    path("<int:number>/", views.MenuDetailView.as_view(), name="detail"),
]
