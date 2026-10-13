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


BLOCKLIST_LOCK = "mail:blocklist:lock"
BLOCKLIST_MAX_MAILBOXES = 500


@shared_task
def enforce_blocklist():
    """Щохвилини переносить у «Спам» нові листи заблокованих відправників (лише «Вхідні»).

    Лише для скриньок із непорожнім списком; worker входить у Dovecot як master-user.
    Обмеження: лист може бути видно у «Вхідних» до ~1 хв (зокрема в IMAP-клієнтах)."""
    from django.core.cache import cache

    from . import imap
    from .models import BlockedSender

    if not cache.add(BLOCKLIST_LOCK, 1, 110):
        return 0  # попередній прохід ще триває
    try:
        by_mailbox: dict = {}
        rows = BlockedSender.objects.filter(mailbox__active=True, mailbox__domain__active=True).select_related("mailbox__domain")
        for row in rows.order_by("mailbox_id", "address"):
            entry = by_mailbox.setdefault(row.mailbox_id, (row.mailbox, []))
            entry[1].append(row.address)
        moved = 0
        for mailbox, entries in list(by_mailbox.values())[:BLOCKLIST_MAX_MAILBOXES]:
            try:
                moved += imap.apply_blocklist_for(mailbox.address, entries)
            except Exception as exc:  # Dovecot недоступний тощо — наступного разу
                log.info("blocklist %s: %s", mailbox.pk, exc.__class__.__name__)
        return moved
    finally:
        cache.delete(BLOCKLIST_LOCK)
