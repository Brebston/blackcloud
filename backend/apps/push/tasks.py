import logging

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.conf import settings

log = logging.getLogger("blackcloud")


@shared_task(bind=True, max_retries=3, acks_late=True, ignore_result=True)
def send_push(self, sub_id: str, endpoint: str, p256dh: str, auth: str, payload: str, ttl: int = 86400, urgency: str = "normal"):
    """Виконується в сервісі pusher (черга "push"): шифрує і надсилає одне повідомлення.
    Доступу до БД не потребує — мертві підписки видаляє окреме завдання в основній черзі."""
    from . import webpush

    if not settings.VAPID_PRIVATE_KEY:
        log.warning("push: VAPID private key is not configured")
        return "disabled"
    try:
        status = webpush.send(
            endpoint, p256dh, auth, payload.encode(), vapid_pem=settings.VAPID_PRIVATE_KEY,
            subject=settings.VAPID_SUBJECT, ttl=ttl, urgency=urgency,
        )
    except (webpush.WebPushError, ValueError):
        # Недозволена адреса або пошкоджені ключі підписки — вона вже ніколи не спрацює
        log.warning("push: invalid subscription %s", sub_id)
        forget_subscription.delay(sub_id)
        return "invalid"
    if 200 <= status < 300:
        return "ok"
    if status in (404, 410):  # браузер відписався або підписка застаріла
        forget_subscription.delay(sub_id)
        return "gone"
    if status in (0, 429) or status >= 500:
        try:
            raise self.retry(countdown=min(30 * 2**self.request.retries, 600))
        except MaxRetriesExceededError:
            log.warning("push: giving up after retries (status %s)", status)
            return "failed"
    log.warning("push: rejected by push service (status %s)", status)
    return "rejected"


@shared_task(ignore_result=True)
def forget_subscription(sub_id: str):
    from .models import PushSubscription

    PushSubscription.objects.filter(pk=sub_id).delete()


@shared_task(ignore_result=True)
def poll_new_mail():
    """Щохвилини (worker): нові листи у «Вхідних» для власників підписок з увімкненою поштою."""
    from .mailwatch import poll

    return poll()
