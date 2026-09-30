from django.urls import path

from . import compose_views, cycle_views, views

app_name = "menus"

urlpatterns = [
    path("", views.MenuListView.as_view(), name="list"),
    path("new/", compose_views.MenuGenerateView.as_view(), name="generate"),
    path("review/", compose_views.MenuReviewView.as_view(), name="review"),
    path("cycle/", cycle_views.CycleImportView.as_view(), name="cycle_import"),
    path("cycle/preview/", cycle_views.CyclePreviewView.as_view(), name="cycle_preview"),
    path("cycle/template.xlsx", cycle_views.CycleTemplateView.as_view(), name="cycle_template"),
    path("<int:number>/", views.MenuDetailView.as_view(), name="detail"),
    path("<int:number>/fit/", compose_views.MenuFitView.as_view(), name="fit"),
]
