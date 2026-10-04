from django.contrib import admin

from .admin_site import admin_site
from .models import AuditLog


class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "user", "ip_address", "target")
    list_filter = ("action",)
    search_fields = ("action", "target", "user__username", "ip_address")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin_site.register(AuditLog, AuditLogAdmin)
