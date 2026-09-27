from django.urls import path

from . import views

app_name = "workflow"

urlpatterns = [
    path("", views.MyTasksView.as_view(), name="my_tasks"),
    path("<int:pk>/", views.TaskDetailView.as_view(), name="task_detail"),
    path("<int:pk>/<str:action>/", views.TaskActionView.as_view(), name="task_action"),
    path("audit/", views.AuditView.as_view(), name="audit"),
    path("notifications/count/", views.NotificationCountView.as_view(), name="notification_count"),
    path("notifications/", views.NotificationListView.as_view(), name="notification_list"),
    path("notifications/<int:pk>/open/", views.NotificationOpenView.as_view(), name="notification_open"),
    path("notifications/read-all/", views.NotificationReadAllView.as_view(), name="notification_read_all"),
]
