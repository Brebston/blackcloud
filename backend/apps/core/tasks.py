from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import AuditLog


@shared_task
def purge_old_audit():
    """Журнал аудиту зберігається AUDIT_RETENTION_DAYS днів (за замовчуванням рік)."""
    cutoff = timezone.now() - timedelta(days=settings.AUDIT_RETENTION_DAYS)
    deleted, _ = AuditLog.objects.filter(created_at__lt=cutoff).delete()
    return deleted


@shared_task(ignore_result=True)
def celery_heartbeat():
    """Мітка «beat і worker живі» для /api/health/deep/ (перевірка celery)."""
    import time

    from django.core.cache import cache

    from .views import CELERY_HEARTBEAT_KEY

    cache.set(CELERY_HEARTBEAT_KEY, time.time(), 24 * 3600)
