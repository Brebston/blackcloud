from django.contrib import admin

from apps.core.admin_mixins import AuditedAdminMixin
from apps.core.admin_site import admin_site

from .models import DavPassword


class DavPasswordAdmin(AuditedAdminMixin, admin.ModelAdmin):
    """Лише перегляд і відкликання (видалення); хеш не показується."""

    list_display = ("user", "label", "created_at", "last_used_at", "last_used_ip")
    search_fields = ("user__username", "label")
    exclude = ("password_hash",)
    readonly_fields = ("user", "label", "created_at", "last_used_at", "last_used_ip")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in ("GET", "HEAD")


admin_site.register(DavPassword, DavPasswordAdmin)
