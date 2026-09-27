from django.urls import path

from . import views

app_name = "reports"
urlpatterns = [
    path("", views.ReportsIndexView.as_view(), name="index"),
    path("menu/<str:day>.pdf", views.MenuPdfView.as_view(), name="menu_pdf"),
    path("accumulative/<str:month>/", views.AccumulativeView.as_view(), name="accumulative"),
]
