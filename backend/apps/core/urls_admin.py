from django.urls import path

from . import views_backup

urlpatterns = [
    path("backups/", views_backup.BackupStatusView.as_view()),
    path("backups/run/", views_backup.BackupRunView.as_view()),
]
