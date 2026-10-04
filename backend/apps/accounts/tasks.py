from celery import shared_task
from django.contrib.sessions.models import Session
from django.utils import timezone

from .models import UserSession


@shared_task
def cleanup_sessions():
    Session.objects.filter(expire_date__lt=timezone.now()).delete()
    live = Session.objects.values_list("session_key", flat=True)
    UserSession.objects.exclude(session_key__in=live).delete()
