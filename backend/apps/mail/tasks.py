import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from django.utils.translation import gettext_noop

from . import confidential

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


@shared_task
def purge_expired_confidential():
    return confidential.purge_expired()


@shared_task
def send_scheduled_mail():
    """Надсилає заплановані листи, час яких настав. Кожен лист «захоплюється» атомарним
    UPDATE pending→sending, тож паралельні воркери не надішлють його двічі."""
    from apps.core.realtime import notify

    from . import imap
    from .models import ScheduledMessage

    Status = ScheduledMessage.Status
    now = timezone.now()
    ids = list(ScheduledMessage.objects.filter(status=Status.PENDING, send_at__lte=now).values_list("pk", flat=True)[:50])
    sent = 0
    for pk in ids:
        if not ScheduledMessage.objects.filter(pk=pk, status=Status.PENDING).update(status=Status.SENDING):
            continue
        item = ScheduledMessage.objects.select_related("mailbox__domain", "user").get(pk=pk)
        try:
            imap.deliver_scheduled(item)
        except Exception as exc:  # SMTP/IMAP недоступні, скриньку вимкнено тощо
            item.attempts += 1
            item.error = exc.__class__.__name__[:200]
            if item.attempts < MAX_ATTEMPTS:
                item.status = Status.PENDING
                item.send_at = timezone.now() + timedelta(minutes=5 * item.attempts)
            else:
                item.status = Status.FAILED
                subject = imap.scheduled_meta(item).get("subject") or ""
                notify(
                    item.user,
                    "mail",
                    gettext_noop("Запланований лист не надіслано"),
                    gettext_noop("«%(subject)s»: спробуйте надіслати його ще раз."),
                    "/mail",
                    params={"subject": subject},
                )
            log.warning("scheduled mail %s failed: %s", pk, item.error)
            item.save(update_fields=["attempts", "error", "status", "send_at"])
            continue
        item.status = Status.SENT
        item.sent_at = timezone.now()
        item.payload_encrypted = ""
        item.save(update_fields=["status", "sent_at", "payload_encrypted"])
        sent += 1
    return sent
