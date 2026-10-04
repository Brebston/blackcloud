from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.core.admin_site import admin_site

from .models import Invite, User, UserSession


class UserAdmin(BaseUserAdmin):
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


admin_site.register(User, UserAdmin)
admin_site.register(UserSession, admin.ModelAdmin)
admin_site.register(Invite, admin.ModelAdmin)
