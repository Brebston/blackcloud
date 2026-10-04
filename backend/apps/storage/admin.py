from django.contrib import admin

from apps.core.admin_site import admin_site

from .models import File, Folder, PublicLink, Share


class FileAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "size", "mime_type", "status", "created_at", "deleted_at")
    list_filter = ("status",)
    search_fields = ("name", "owner__username", "sha256")
    exclude = ("wrapped_key",)
    readonly_fields = ("size", "sha256", "mime_type", "chunks_received", "chunk_size", "key_version")


admin_site.register(File, FileAdmin)
admin_site.register(Folder, admin.ModelAdmin)
admin_site.register(Share, admin.ModelAdmin)
admin_site.register(PublicLink, admin.ModelAdmin)
