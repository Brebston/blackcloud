from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.core.admin_mixins import AuditedAdminMixin, ReadOnlyAdmin
from apps.core.admin_site import admin_site

from .models import Invite, User

PRIVILEGE_FIELDS = ("is_staff", "is_superuser", "groups", "user_permissions")


class UserAdmin(AuditedAdminMixin, BaseUserAdmin):
    ordering = ("username",)
    list_display = ("username", "email", "is_active", "is_staff", "used_bytes", "quota_bytes", "date_joined")
    search_fields = ("username", "email", "display_name")
    readonly_fields = ("used_bytes", "last_login", "date_joined", "password_changed_at")
    fieldsets = (
        (None, {"fields": ("username", "email", "display_name", "password")}),
        ("Квота", {"fields": ("quota_bytes", "used_bytes")}),
        ("Права", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Дати", {"fields": ("last_login", "date_joined", "password_changed_at")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("username", "email", "password1", "password2")}),)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            # Лише суперкористувач змінює права; інші адміністратори не можуть себе підвищити
            fields += list(PRIVILEGE_FIELDS)
        return fields

    def has_change_permission(self, request, obj=None):
        if obj is not None and (obj.is_staff or obj.is_superuser) and not request.user.is_superuser:
            return request.method in ("GET", "HEAD")
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and (obj.is_staff or obj.is_superuser) and not request.user.is_superuser:
            return False
        return super().has_delete_permission(request, obj)


class InviteAdmin(ReadOnlyAdmin):
    list_display = ("email", "created_at", "expires_at", "used_at")
    exclude = ("token_hash",)


admin_site.register(User, UserAdmin)
# UserSession навмисно не зареєстровано: ключ сесії = повний доступ до акаунта
admin_site.register(Invite, InviteAdmin)
