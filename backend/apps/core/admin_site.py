from django.conf import settings
from django.contrib import admin
from django.shortcuts import redirect


class SecureAdminSite(admin.AdminSite):
    """Адмінка Django: вхід лише через SPA (з 2FA), власної форми логіну немає."""

    site_header = "BlackCloud — адміністрування"
    site_title = "BlackCloud"
    index_title = "Керування"

    def has_permission(self, request):
        from .permissions import staff_mfa_ok

        return staff_mfa_ok(request)

    def login(self, request, extra_context=None):
        return redirect(f"/login?next=/{settings.ADMIN_URL_PREFIX}/")


admin_site = SecureAdminSite(name="bcadmin")
