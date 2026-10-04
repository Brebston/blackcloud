from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("sessions", views.SessionsViewSet, basename="session")

urlpatterns = [
    path("profile/", views.ProfileView.as_view()),
    path("preferences/", views.PreferencesView.as_view()),
    path("password/", views.ChangePasswordView.as_view()),
    path("security/", views.SecurityView.as_view()),
    path("security/totp/setup/", views.TotpSetupView.as_view()),
    path("security/totp/confirm/", views.TotpConfirmView.as_view()),
    path("security/2fa/disable/", views.TwoFactorDisableView.as_view()),
    path("security/backup-codes/", views.BackupCodesView.as_view()),
    path("audit/", views.MyAuditLogView.as_view()),
] + router.urls
