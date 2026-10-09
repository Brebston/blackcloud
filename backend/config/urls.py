from django.conf import settings
from django.urls import include, path

from apps.core.admin_site import admin_site
from apps.core.views import health
from apps.storage.views import PreviewServeView

urlpatterns = [
    path("api/health/", health),
    path("api/auth/", include("apps.accounts.urls_auth")),
    path("api/account/", include("apps.accounts.urls_account")),
    path("api/admin/", include("apps.accounts.urls_admin")),
    path("api/admin/", include("apps.storage.urls_admin")),
    path("api/admin/", include("apps.mail.urls_admin")),
    path("api/users/", include("apps.accounts.urls_users")),
    path("api/files/", include("apps.storage.urls")),
    path("api/preview/<str:ticket>/", PreviewServeView.as_view()),
    path("api/public/calendar/", include("apps.calendars.urls_public")),
    path("api/public/confidential/", include("apps.mail.urls_public")),
    path("api/public/", include("apps.storage.urls_public")),
    path("api/calendars/", include("apps.calendars.urls")),
    path("api/chat/", include("apps.chat.urls")),
    path("api/mail/", include("apps.mail.urls")),
    path("api/office/", include("apps.storage.urls_office")),
    path("api/notifications/", include("apps.core.urls")),
    path(f"{settings.ADMIN_URL_PREFIX}/", admin_site.urls),
]
