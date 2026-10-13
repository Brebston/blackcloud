from celery import shared_task
from django.contrib.sessions.models import Session
from django.utils import timezone

from .models import UserSession


@shared_task
def cleanup_sessions():
    Session.objects.filter(expire_date__lt=timezone.now()).delete()
    live = Session.objects.values_list("session_key", flat=True)
    UserSession.objects.exclude(session_key__in=live).delete()


@shared_task(acks_late=True)
def process_avatar_task(user_id: str, pending_id: str):
    """Обробка фото профілю на worker (ізольований thumbnailer). Див. avatars.py."""
    from .avatars import process_avatar

    process_avatar(user_id, pending_id)
