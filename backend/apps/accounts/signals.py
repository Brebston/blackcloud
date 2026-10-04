from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.audit import client_ip

from .models import Preferences, User, UserSession


@receiver(post_save, sender=User)
def create_preferences(sender, instance, created, **kwargs):
    if created:
        Preferences.objects.get_or_create(user=instance)


@receiver(user_logged_in)
def track_session(sender, request, user, **kwargs):
    if request is None or not request.session.session_key:
        return
    UserSession.objects.update_or_create(
        session_key=request.session.session_key,
        defaults={
            "user": user,
            "ip_address": client_ip(request),
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300],
        },
    )


@receiver(user_logged_out)
def untrack_session(sender, request, user, **kwargs):
    if request is not None and request.session.session_key:
        UserSession.objects.filter(session_key=request.session.session_key).delete()
