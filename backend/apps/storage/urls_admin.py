from django.urls import path

from . import views_admin

urlpatterns = [
    path("quarantine/", views_admin.QuarantineListView.as_view()),
    path("quarantine/<uuid:pk>/rescan/", views_admin.QuarantineRescanView.as_view()),
    path("quarantine/<uuid:pk>/purge/", views_admin.QuarantinePurgeView.as_view()),
    path("antivirus/", views_admin.AntivirusView.as_view()),
    path("antivirus/rescan-failed/", views_admin.AntivirusRescanFailedView.as_view()),
]
