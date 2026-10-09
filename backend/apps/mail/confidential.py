"""Конфіденційний режим (за зразком Gmail).

Справжній вміст листа не залишає сервер: отримувач одержує лише посилання
https://<домен>/c#<токен>. Вміст зберігається зашифрованим (Fernet, FIELD_ENCRYPTION_KEY),
доступний до терміну дії або до відкликання, за потреби — з кодом доступу,
який відправник передає окремо (наприклад, телефоном).

Обмеження (чесно): заборонити отримувачу зробити знімок екрана або переписати
текст неможливо — як і в Gmail. Вкладення в конфіденційному режимі не підтримуються.
"""

import hashlib
import secrets
from datetime import timedelta
from html import escape

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core.crypto import encrypt_str

from .models import ConfidentialMessage

ALLOWED_DAYS = (1, 7, 30, 90)
MAX_HTML_BYTES = 5 * 1024 * 1024


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(*, sender, from_address: str, recipients: list[str], subject: str, html: str, days, with_passcode: bool):
    try:
        days = int(days or 7)
    except (TypeError, ValueError):
        days = 0
    if days not in ALLOWED_DAYS:
        raise ValidationError({"confidential_days": [f"Допустимі терміни: {', '.join(map(str, ALLOWED_DAYS))} днів."]})
    if len(html.encode()) > MAX_HTML_BYTES:
        raise ValidationError({"html": ["Конфіденційний лист завеликий (максимум 5 МБ разом із зображеннями)."]})
    token = secrets.token_urlsafe(32)
    passcode = f"{secrets.randbelow(10**6):06d}" if with_passcode else ""
    msg = ConfidentialMessage.objects.create(
        sender=sender,
        from_address=from_address,
        recipients=", ".join(recipients)[:5000],
        subject=subject,
        html_encrypted=encrypt_str(html),
        token_hash=hash_token(token),
        passcode_hash=make_password(passcode) if passcode else "",
        expires_at=timezone.now() + timedelta(days=days),
    )
    return msg, token, passcode


def notice(msg: ConfidentialMessage, token: str, sender_name: str) -> tuple[str, str]:
    """Текст і HTML листа-повідомлення, який реально надсилається отримувачам."""
    url = f"https://{settings.DOMAIN}/c#{token}"
    expires = timezone.localtime(msg.expires_at).strftime("%d.%m.%Y %H:%M")
    passcode_line = "Для відкриття потрібен код доступу — його повідомить відправник." if msg.passcode_hash else ""
    text = (
        f"{sender_name} надсилає вам конфіденційний лист.\n\n"
        f"Відкрити: {url}\n"
        f"Доступний до: {expires}\n"
        f"{passcode_line}\n\n"
        "Вміст не зберігається у вашій поштовій скриньці; відправник може відкликати доступ."
    ).strip()
    html = (
        "<div style='font-family:system-ui,sans-serif;font-size:14px'>"
        f"<p><strong>{escape(sender_name)}</strong> надсилає вам конфіденційний лист.</p>"
        f"<p><a href='{escape(url)}'>Відкрити лист</a></p>"
        f"<p style='color:#666'>Доступний до: {escape(expires)}"
        + (f"<br>{escape(passcode_line)}" if passcode_line else "")
        + "</p><p style='color:#666;font-size:12px'>Вміст не зберігається у вашій поштовій скриньці; "
        "відправник може відкликати доступ.</p></div>"
    )
    return text, html


def purge_expired() -> int:
    """Після закінчення терміну вміст видаляється (метадані лишаються для журналу відправника)."""
    return (
        ConfidentialMessage.objects.filter(expires_at__lt=timezone.now())
        .exclude(html_encrypted="")
        .update(html_encrypted="")
    )
