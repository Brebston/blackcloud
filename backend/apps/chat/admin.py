from django.contrib import admin

from apps.core.admin_site import admin_site

from .models import Conversation

admin_site.register(Conversation, admin.ModelAdmin)
