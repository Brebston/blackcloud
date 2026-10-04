from django.conf import settings
from django.contrib import admin
from django.shortcuts import redirect


class SecureAdminSite(admin.AdminSite):
    """Адмінка Django: вхід лише через SPA (з 2FA), власної форми логіну немає."""

    site_header = "BlackCloud — адміністрування"
    site_title = "BlackCloud"
    index_title = "Керування"

    def has_permission(self, request):
        user = request.user
        if not (user.is_active and user.is_staff):
            return False
        if settings.REQUIRE_2FA_FOR_STAFF and not user.has_2fa:
            return False
        return True

    def login(self, request, extra_context=None):
        return redirect(f"/login?next=/{settings.ADMIN_URL_PREFIX}/")


admin_site = SecureAdminSite(name="bcadmin")
