from django.urls import path

from . import ai_views, audit_views, views

app_name = "catalog"

urlpatterns = [
    path("dishes/", views.DishListView.as_view(), name="dish_list"),
    path("dishes/new/", views.DishEditView.as_view(), name="dish_create"),
    path("dishes/draft/", ai_views.DishDraftStartView.as_view(), name="dish_draft_start"),
    path("dishes/draft/<int:pk>/", ai_views.DishDraftView.as_view(), name="dish_draft"),
    path("products/suggest/", ai_views.ProductSuggestStartView.as_view(), name="product_suggest_start"),
    path("products/suggest/<int:pk>/", ai_views.ProductSuggestView.as_view(), name="product_suggest"),
    path("dishes/<int:pk>/", views.DishDetailView.as_view(), name="dish_detail"),
    path("dishes/<int:pk>/edit/", views.DishEditView.as_view(), name="dish_edit"),
    path("dishes/<int:pk>/verify/", views.DishVerifyView.as_view(), name="dish_verify"),
    path("dishes/<int:pk>/ingredients/add/", views.IngredientAddView.as_view(), name="ingredient_add"),
    path("dishes/<int:pk>/ingredients/<int:ing_pk>/", views.IngredientUpdateView.as_view(), name="ingredient_update"),
    path("dishes/<int:pk>/ingredients/<int:ing_pk>/delete/", views.IngredientDeleteView.as_view(), name="ingredient_delete"),
    path("products/", views.ProductListView.as_view(), name="product_list"),
    path("products/new/", views.ProductEditView.as_view(), name="product_create"),
    path("products/<int:pk>/edit/", views.ProductEditView.as_view(), name="product_edit"),
    path("diets/", views.DietListView.as_view(), name="diet_list"),
    path("audit/", audit_views.AuditView.as_view(), name="audit"),
    path("audit/<int:pk>/dismiss/", audit_views.FindingDismissView.as_view(), name="finding_dismiss"),
]
