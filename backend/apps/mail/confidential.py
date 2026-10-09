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
from django.utils.translation import gettext as _
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
        raise ValidationError({"confidential_days": [_("Допустимі терміни: %(days)s днів.") % {"days": ", ".join(map(str, ALLOWED_DAYS))}]})
    if len(html.encode()) > MAX_HTML_BYTES:
        raise ValidationError({"html": [_("Конфіденційний лист завеликий (максимум 5 МБ разом із зображеннями).")]})
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
    """Текст і HTML листа-повідомлення, який реально надсилається отримувачам.

    Мова зовнішнього отримувача невідома, тому повідомлення двомовне: спершу українською, потім англійською."""
    url = f"https://{settings.DOMAIN}/c#{token}"
    expires = timezone.localtime(msg.expires_at).strftime("%d.%m.%Y %H:%M")
    passcode_uk = "Для відкриття потрібен код доступу — його повідомить відправник." if msg.passcode_hash else ""
    passcode_en = "A passcode is required to open it; the sender will share it with you." if msg.passcode_hash else ""
    text_uk = (
        f"{sender_name} надсилає вам конфіденційний лист.\n\n"
        f"Відкрити: {url}\n"
        f"Доступний до: {expires}\n"
        f"{passcode_uk}\n\n"
        "Вміст не зберігається у вашій поштовій скриньці; відправник може відкликати доступ."
    ).strip()
    text_en = (
        f"{sender_name} has sent you a confidential message.\n\n"
        f"Open: {url}\n"
        f"Available until: {expires}\n"
        f"{passcode_en}\n\n"
        "The content is not stored in your mailbox; the sender can revoke access at any time."
    ).strip()
    text = f"{text_uk}\n\n{'-' * 40}\n\n{text_en}"

    def html_block(intro: str, open_label: str, until_label: str, passcode_line: str, footer: str) -> str:
        return (
            f"<p><strong>{escape(sender_name)}</strong> {intro}</p>"
            f"<p><a href='{escape(url)}'>{open_label}</a></p>"
            f"<p style='color:#666'>{until_label}: {escape(expires)}"
            + (f"<br>{escape(passcode_line)}" if passcode_line else "")
            + f"</p><p style='color:#666;font-size:12px'>{footer}</p>"
        )

    html = (
        "<div style='font-family:system-ui,sans-serif;font-size:14px'>"
        + html_block(
            "надсилає вам конфіденційний лист.",
            "Відкрити лист",
            "Доступний до",
            passcode_uk,
            "Вміст не зберігається у вашій поштовій скриньці; відправник може відкликати доступ.",
        )
        + "<hr style='border:none;border-top:1px solid #ddd;margin:16px 0'>"
        + html_block(
            "has sent you a confidential message.",
            "Open message",
            "Available until",
            passcode_en,
            "The content is not stored in your mailbox; the sender can revoke access at any time.",
        )
        + "</div>"
    )
    return text, html


def purge_expired() -> int:
    """Після закінчення терміну вміст видаляється (метадані лишаються для журналу відправника)."""
    return (
        ConfidentialMessage.objects.filter(expires_at__lt=timezone.now())
        .exclude(html_encrypted="")
        .update(html_encrypted="")
    )
