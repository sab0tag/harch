from django.urls import path

from . import views

app_name = "consumption"
urlpatterns = [
    path("kitchen/", views.KitchenView.as_view(), name="kitchen"),
    path("kitchen/<str:day>/", views.KitchenView.as_view(), name="kitchen_day"),
    path("kitchen/<str:day>/dish/<str:slot>/<int:card_no>/", views.DishCardView.as_view(), name="dish_card"),
    path("consumption/", views.ConsumptionView.as_view(), name="report"),
    path("write-off/", views.WriteOffView.as_view(), name="write_off"),
    path("write-off/<str:day>/", views.WriteOffView.as_view(), name="write_off_day"),
]
