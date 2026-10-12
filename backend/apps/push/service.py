"""Постановка push-сповіщень у чергу. Викликається з backend/worker; саме надсилання
виконує окремий сервіс pusher (черга "push"), бо лише він має вихід в інтернет."""

import json
import logging
from functools import lru_cache

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.i18n import language_of

from . import webpush
from .models import PushSubscription

log = logging.getLogger("blackcloud")

KIND_FIELDS = {"mail": "notify_mail", "chat": "notify_chat", "other": "notify_other"}
# Межа відкритого тексту: один запис aes128gcm (4096 байтів) з запасом
MAX_PAYLOAD_BYTES = 3000
TITLE_MAX, BODY_MAX = 120, 300
URGENCY = {"chat": "high", "mail": "normal", "other": "normal"}


@lru_cache(maxsize=4)
def _normalized_key(value: str) -> str:
    try:
        return webpush.normalize_public_key(value)
    except Exception:
        log.warning("VAPID public key is invalid")
        return ""


def vapid_public_key() -> str:
    """Публічний ключ VAPID (base64url) або "" — якщо push не налаштовано."""
    return _normalized_key(settings.VAPID_PUBLIC_KEY or "")


def build_payload(kind: str, title: str, body: str = "", url: str = "/", tag: str = "") -> str:
    title, body = (title or "")[:TITLE_MAX], (body or "")[:BODY_MAX]
    if not url.startswith("/") or url.startswith("//"):
        url = "/"  # лише відносні адреси власного сайту
    data = {"title": title, "body": body, "url": url[:500], "tag": tag[:64], "kind": kind}
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    while len(raw.encode()) > MAX_PAYLOAD_BYTES and data["body"]:
        data["body"] = data["body"][: len(data["body"]) // 2]
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return raw


def _enqueue(subs, kind, payload_for) -> int:
    from .tasks import send_push

    count = 0
    for sub in subs:
        try:
            send_push.delay(
                str(sub.pk), sub.endpoint, sub.p256dh, sub.auth, payload_for(sub), urgency=URGENCY.get(kind, "normal")
            )
            count += 1
        except Exception:  # брокер недоступний — сповіщення в застосунку все одно є
            log.warning("push enqueue failed", exc_info=True)
    if count:
        PushSubscription.objects.filter(pk__in=[s.pk for s in subs]).update(last_used_at=timezone.now())
    return count


def send_to_user(user, kind: str, title: str, body: str = "", url: str = "/", tag: str = "", *, private: bool = False) -> int:
    """Ставить у чергу сповіщення на всі пристрої користувача, де увімкнено події kind.
    private=True — вміст (текст повідомлення, тема листа) приховується на пристроях,
    де вимкнено попередній перегляд. Тексти вже мають бути мовою користувача."""
    field = KIND_FIELDS.get(kind)
    if (field is None and kind != "test") or not getattr(user, "is_active", False) or not vapid_public_key():
        return 0
    # "test" — перевірка з налаштувань: на всі пристрої незалежно від вибраних подій
    subs = list(PushSubscription.objects.filter(user=user, **({field: True} if field else {})))
    if not subs:
        return 0
    full = build_payload(kind, title, body, url, tag)
    hidden = full
    if private:
        with language_of(user):
            generic = _("Новий лист") if kind == "mail" else _("Нове повідомлення")
        hidden = build_payload(kind, generic, "", url, tag)
    return _enqueue(subs, kind, lambda s: full if (s.show_previews or not private) else hidden)


def send_later(user, kind: str, title: str, body: str = "", url: str = "/", tag: str = "", *, private: bool = False) -> None:
    """Те саме після фіксації транзакції; помилки не впливають на основну дію."""

    def run():
        try:
            send_to_user(user, kind, title, body, url, tag, private=private)
        except Exception:
            log.warning("push failed", exc_info=True)

    transaction.on_commit(run)


def notify_chat_message(conversation, message, sender) -> None:
    """Нове повідомлення чату → push іншим учасникам (крім тих, хто вимкнув звук розмови)."""
    from apps.chat.models import Participant

    recipients = (
        Participant.objects.filter(conversation=conversation, muted=False, user__is_active=True)
        .exclude(user=sender)
        .select_related("user")
    )
    sender_name = (getattr(sender, "display_name", "") or sender.username)[:80]
    text = (message.body or "").strip().replace("\n", " ")
    for p in recipients:
        with language_of(p.user):
            body = text[:100] + ("…" if len(text) > 100 else "") if text else ("📎 " + _("Вкладення"))
            if conversation.is_group:
                title = conversation.title or _("Групова розмова")
                body = f"{sender_name}: {body}"
            else:
                title = sender_name
        send_later(p.user, "chat", title, body, f"/chat/{conversation.pk}", f"chat-{conversation.pk.hex[:12]}", private=True)
