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


def notify(user, kind: str, title: str, body: str = "", link: str = ""):
    from .models import Notification

    n = Notification.objects.create(user=user, kind=kind, title=title, body=body, link=link)
    push_to_user(
        user.pk,
        "notification",
        {"id": str(n.id), "kind": kind, "title": title, "body": body, "link": link, "created_at": n.created_at.isoformat()},
    )
    return n
