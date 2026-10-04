from django.contrib import admin

from apps.core.admin_site import admin_site

from .models import Alias, MailDomain, Mailbox


class MailboxAdmin(admin.ModelAdmin):
    list_display = ("__str__", "user", "quota_mb", "active", "client_password_set_at")
    search_fields = ("local_part", "user__username")
    exclude = ("client_password_hash",)


admin_site.register(MailDomain, admin.ModelAdmin)
admin_site.register(Mailbox, MailboxAdmin)
admin_site.register(Alias, admin.ModelAdmin)
