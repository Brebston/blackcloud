from django.contrib import admin

from apps.core.admin_site import admin_site

from .models import PushSubscription


@admin.register(PushSubscription, site=admin_site)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "user_agent", "created_at", "last_used_at")
    list_select_related = ("user",)
    search_fields = ("user__username",)
    readonly_fields = ("endpoint", "p256dh", "auth", "user_agent", "created_at", "last_used_at")
