from django.contrib import admin

from apps.core.admin_site import admin_site

from .models import Calendar, Event

admin_site.register(Calendar, admin.ModelAdmin)
admin_site.register(Event, admin.ModelAdmin)
