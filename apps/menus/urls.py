from django.urls import path

from . import compose_views, views

app_name = "menus"

urlpatterns = [
    path("", views.MenuListView.as_view(), name="list"),
    path("new/", compose_views.MenuGenerateView.as_view(), name="generate"),
    path("review/", compose_views.MenuReviewView.as_view(), name="review"),
    path("<int:number>/", views.MenuDetailView.as_view(), name="detail"),
    path("<int:number>/fit/", compose_views.MenuFitView.as_view(), name="fit"),
]
