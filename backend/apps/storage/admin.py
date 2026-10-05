from django.contrib import admin

from apps.core.admin_mixins import AuditedAdminMixin, ReadOnlyAdmin
from apps.core.admin_site import admin_site

from .models import File, Folder, PublicLink, Share


class FileAdmin(AuditedAdminMixin, admin.ModelAdmin):
    """Через Django admin можна лише перейменувати файл. Статус антивірусу, власник,
    хеш і MIME змінюються тільки кодом: інакше заражений файл можна було б «розблокувати»."""

    list_display = ("name", "owner", "size", "mime_type", "status", "created_at", "deleted_at")
    list_filter = ("status",)
    search_fields = ("name", "owner__username", "sha256")
    exclude = ("wrapped_key",)
    readonly_fields = (
        "owner",
        "folder",
        "status",
        "scan_detail",
        "size",
        "sha256",
        "mime_type",
        "chunks_received",
        "chunk_size",
        "key_version",
        "content_version",
        "has_thumbnail",
        "deleted_at",
    )

    def has_add_permission(self, request):
        return False


class FolderAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "owner", "parent")
    readonly_fields = ("owner", "parent")

    def has_add_permission(self, request):
        return False


class PublicLinkAdmin(ReadOnlyAdmin):
    list_display = ("file", "owner", "created_at", "expires_at", "revoked_at")
    exclude = ("token_hash", "password_hash")


admin_site.register(File, FileAdmin)
admin_site.register(Folder, FolderAdmin)
admin_site.register(Share, ReadOnlyAdmin)
admin_site.register(PublicLink, PublicLinkAdmin)
