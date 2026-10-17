from django.conf import settings
from django.urls import include, path

from apps.core.admin_site import admin_site
from apps.core.views import HealthDeepView, health
from apps.mail.mta_sts import mta_sts_policy
from apps.storage.views import PreviewServeView

urlpatterns = [
    # MTA-STS: віддається лише на хості mta-sts.<MAIL_DOMAIN> (apps/mail/mta_sts.py)
    path(".well-known/mta-sts.txt", mta_sts_policy),
    path("api/health/", health),
    path("api/health/deep/", HealthDeepView.as_view()),
    path("api/auth/", include("apps.accounts.urls_auth")),
    path("api/account/", include("apps.accounts.urls_account")),
    path("api/admin/", include("apps.accounts.urls_admin")),
    path("api/admin/", include("apps.storage.urls_admin")),
    path("api/admin/", include("apps.mail.urls_admin")),
    path("api/admin/", include("apps.core.urls_admin")),
    path("api/users/", include("apps.accounts.urls_users")),
    path("api/files/", include("apps.storage.urls")),
    path("api/photos/", include("apps.photos.urls")),
    path("api/preview/<str:ticket>/", PreviewServeView.as_view()),
    path("api/public/calendar/", include("apps.calendars.urls_public")),
    path("api/public/confidential/", include("apps.mail.urls_public")),
    path("api/public/", include("apps.storage.urls_public")),
    path("api/calendars/", include("apps.calendars.urls")),
    path("api/chat/", include("apps.chat.urls")),
    path("api/mail/", include("apps.mail.urls")),
    path("api/office/", include("apps.storage.urls_office")),
    path("api/notifications/", include("apps.core.urls")),
    path("api/push/", include("apps.push.urls")),
    path("api/dav/", include("apps.dav.urls_api")),
    # WebDAV: /dav/… (Basic auth з паролем застосунку, без сесій і CSRF)
    path("", include("apps.dav.urls")),
    path(f"{settings.ADMIN_URL_PREFIX}/", admin_site.urls),
]
