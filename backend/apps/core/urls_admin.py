from django.urls import path

from . import views_backup, views_status

urlpatterns = [
    path("backups/", views_backup.BackupStatusView.as_view()),
    path("backups/run/", views_backup.BackupRunView.as_view()),
    path("status/", views_status.ServerStatusView.as_view()),
]
