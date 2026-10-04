from django.apps import AppConfig


class CalendarsConfig(AppConfig):
    name = "apps.calendars"
    label = "calendars"
    verbose_name = "Календарі"

    def ready(self):
        from django.db.models.signals import post_save

        from apps.accounts.models import User

        from .models import Calendar

        def create_default_calendar(sender, instance, created, **kwargs):
            if created:
                Calendar.objects.get_or_create(owner=instance, name="Особистий", defaults={"color": "#7F77DD"})

        post_save.connect(create_default_calendar, sender=User, weak=False, dispatch_uid="bc_default_calendar")
