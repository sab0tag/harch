from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("catalog/", include("apps.catalog.urls")),
    path("menus/", include("apps.menus.urls")),
    path("org/", include("apps.orgstructure.urls")),
    path("tasks/", include("apps.workflow.urls")),
    path("orders/", include("apps.orders.urls")),
    path("plan/", include("apps.planning.urls")),
    path("", include("apps.core.urls")),
]
