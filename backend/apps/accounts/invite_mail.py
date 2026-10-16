"""Лист із запрошенням до реєстрації.

Надсилається через внутрішній Postfix (той самий шлях, що й веб-пошта: порт SMTP_PORT,
дозволений лише контейнерам backend/worker), тож лист підписується DKIM домену пошти.
Мова одержувача невідома — текст українською й англійською в одному листі.
Посилання містить одноразовий токен; у БД зберігається лише його хеш.
"""

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from html import escape
from urllib.parse import urlencode

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("blackcloud")


class InviteMailError(Exception):
    pass


def invite_url(token: str) -> str:
    # Лише токен: email у посиланні потрапив би в журнали доступу веб-сервера
    return f"https://{settings.DOMAIN}/register?{urlencode({'invite': token})}"


def _inviter_name(user) -> str:
    return (getattr(user, "display_name", "") or getattr(user, "username", "") or "").strip()


def build(invite, token: str, inviter) -> EmailMessage:
    site = settings.SITE_NAME
    url = invite_url(token)
    until = timezone.localtime(invite.expires_at).strftime("%d.%m.%Y %H:%M")
    who = _inviter_name(inviter)
    who_uk = f"{who} запрошує вас" if who else "Вас запрошено"
    who_en = f"{who} invites you" if who else "You are invited"

    text = (
        f"{who_uk} створити акаунт у {site} ({settings.DOMAIN}): файли, пошта, календар і чат.\n\n"
        f"Зареєструватися: {url}\n\n"
        f"Посилання дійсне до {until} і працює лише один раз для адреси {invite.email}.\n"
        "Якщо ви не чекали цього листа — просто проігноруйте його.\n\n"
        "— — —\n\n"
        f"{who_en} to create an account at {site} ({settings.DOMAIN}): files, mail, calendar and chat.\n\n"
        f"Sign up: {url}\n\n"
        f"The link is valid until {until} and works once for {invite.email}.\n"
        "If you weren't expecting this email, just ignore it.\n"
    )
    e = escape
    button = (
        f'<p style="margin:24px 0"><a href="{e(url)}" style="background:#2e6b34;color:#fff;padding:12px 22px;'
        f'border-radius:8px;text-decoration:none;font-weight:600;display:inline-block">{{label}}</a></p>'
    )
    html = (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:15px;line-height:1.5;'
        'color:#1f2328;max-width:560px;margin:0 auto;padding:24px">'
        f'<h2 style="margin:0 0 12px">{e(site)}</h2>'
        f"<p>{e(who_uk)} створити акаунт у <b>{e(site)}</b> ({e(settings.DOMAIN)}): файли, пошта, календар і чат.</p>"
        + button.replace("{label}", "Зареєструватися")
        + f'<p style="color:#59636e;font-size:13px">Посилання дійсне до {e(until)} і працює лише один раз для адреси '
        f"{e(invite.email)}. Якщо ви не чекали цього листа — просто проігноруйте його.</p>"
        '<hr style="border:none;border-top:1px solid #d1d9e0;margin:24px 0">'
        f"<p>{e(who_en)} to create an account at <b>{e(site)}</b> ({e(settings.DOMAIN)}): files, mail, calendar and chat.</p>"
        + button.replace("{label}", "Sign up")
        + f'<p style="color:#59636e;font-size:13px">The link is valid until {e(until)} and works once for {e(invite.email)}. '
        "If you weren't expecting this email, just ignore it.</p>"
        f'<p style="color:#59636e;font-size:12px;word-break:break-all">{e(url)}</p>'
        "</div>"
    )

    msg = EmailMessage()
    msg["Subject"] = f"Запрошення до {site} / Invitation to {site}"
    msg["From"] = formataddr((site, settings.SYSTEM_MAIL_FROM))
    msg["To"] = invite.email
    reply_to = _reply_to(inviter)
    if reply_to:
        msg["Reply-To"] = formataddr((who, reply_to)) if who else reply_to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=settings.MAIL_DOMAIN)
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


def _reply_to(inviter) -> str:
    """Відповідь на лист — на першу скриньку адміністратора, який запросив (якщо є)."""
    try:
        mb = inviter.mailboxes.select_related("domain").filter(active=True).first()
    except Exception:  # noqa: BLE001 — без пошти просто без Reply-To
        return ""
    return mb.address if mb else ""


def send(invite, token: str, inviter) -> None:
    if not invite.email:
        raise InviteMailError("no_email")
    msg = build(invite, token, inviter)
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as smtp:
            smtp.sendmail(settings.SYSTEM_MAIL_FROM, [invite.email], msg.as_bytes())
    except smtplib.SMTPRecipientsRefused as exc:
        raise InviteMailError("recipient_refused") from exc
    except (OSError, smtplib.SMTPException) as exc:
        logger.warning("invite mail failed: %s", type(exc).__name__)
        raise InviteMailError("smtp_unavailable") from exc
