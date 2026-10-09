"""Надсилання подій користувачам через WebSocket (Channels)."""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger("blackcloud")


def user_group(user_id) -> str:
    return f"user_{str(user_id).replace('-', '')}"


def push_to_user(user_id, event: str, payload: dict) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(user_group(user_id), {"type": "push", "event": event, "payload": payload})
    except Exception:
        logger.warning("realtime push failed", exc_info=True)


def session_group(session_key: str) -> str:
    import hashlib

    return "sess_" + hashlib.sha256(session_key.encode()).hexdigest()[:32]


def _close(group: str) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(group, {"type": "force_close"})
    except Exception:
        logger.warning("realtime close failed", exc_info=True)


def close_user_sockets(user_id) -> None:
    """Закрити всі WebSocket користувача (деактивація, скидання 2FA, зміна пароля)."""
    _close(user_group(user_id))


def close_session_sockets(session_keys) -> None:
    """Закрити WebSocket конкретних сесій (вихід, відкликання пристрою)."""
    for key in session_keys:
        if key:
            _close(session_group(key))


def alert_staff(title: str, body: str = "", params: dict | None = None) -> None:
    """Сповіщення всім адміністраторам про підозрілу активність (шаблони — як у notify)."""
    from django.contrib.auth import get_user_model

    for admin_user in get_user_model().objects.filter(is_staff=True, is_active=True):
        notify(admin_user, "security", title, body, "/admin", params=params)


def notify(user, kind: str, title: str, body: str = "", link: str = "", params: dict | None = None):
    """Сповіщення користувачу мовою з його налаштувань.

    title/body — шаблони, позначені gettext_noop (українською, з %(name)s); значення — у params.
    Переклад виконується тут, бо сповіщення зберігається в БД і надсилається поза запитом користувача."""
    from .i18n import language_of, render
    from .models import Notification

    with language_of(user):
        title, body = render(title, params), render(body, params)
    n = Notification.objects.create(user=user, kind=kind, title=title, body=body, link=link)
    push_to_user(
        user.pk,
        "notification",
        {"id": str(n.id), "kind": kind, "title": title, "body": body, "link": link, "created_at": n.created_at.isoformat()},
    )
    return n
