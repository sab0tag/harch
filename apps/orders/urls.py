from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("", views.OrdersEntryView.as_view(), name="entry"),
    path("summary/", views.OrderSummaryView.as_view(), name="summary"),
    path("<int:dept_id>/<str:day>/", views.OrderEditView.as_view(), name="edit"),
    path("<int:dept_id>/<str:day>/corrections/new/", views.CorrectionNewView.as_view(), name="correction_new"),
    path("corrections/<int:pk>/", views.CorrectionView.as_view(), name="correction"),
]
