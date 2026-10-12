"""Виявлення нових листів для push-сповіщень.

Dovecot не повідомляє застосунок про доставку, тому worker щохвилини питає STATUS
«Вхідних» (UIDNEXT/UIDVALIDITY) — лише для скриньок, власники яких мають підписки
з увімкненою поштою. Останній побачений UIDNEXT зберігається в кеші (Redis); якщо кеш
очищено, перший прохід лише запам'ятовує стан, без сповіщень. Тека «Спам» не
перевіряється (спам туди потрапляє фільтром і сповіщень не дає).
"""

import email
import logging
import re
from email.policy import default as default_policy
from email.utils import parseaddr

from django.core.cache import cache
from django.utils.translation import gettext as _

from apps.core.i18n import language_of

log = logging.getLogger("blackcloud")

MAX_PER_RUN = 5  # листів на скриньку за один прохід
MAX_MAILBOXES = 500
STATE_TTL = 30 * 24 * 3600
LOCK_KEY = "push:mail:lock"


def _state_key(mailbox) -> str:
    return f"push:mail:{mailbox.pk}"


def _status(conn):
    from apps.mail import imap

    typ, data = conn.status(imap.quote("INBOX"), "(UIDNEXT UIDVALIDITY)")
    if typ != "OK" or not data or not data[0]:
        return None
    raw = data[0] if isinstance(data[0], bytes) else str(data[0]).encode()
    nxt, val = re.search(rb"UIDNEXT (\d+)", raw), re.search(rb"UIDVALIDITY (\d+)", raw)
    if not nxt or not val:
        return None
    return int(val.group(1)), int(nxt.group(1))


def _fetch_new(conn, first: int, last: int) -> list[dict]:
    from apps.mail import imap

    imap._select(conn, "INBOX", readonly=True)
    typ, data = conn.uid("FETCH", f"{first}:{last}", "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
    items = []
    for part in data or []:
        if not isinstance(part, tuple):
            continue
        meta, headers = part[0], part[1]
        uid = re.search(rb"UID (\d+)", meta)
        if not uid or not (first <= int(uid.group(1)) <= last):
            continue
        flags = re.search(rb"FLAGS \(([^)]*)\)", meta)
        if flags and b"\\Seen" in flags.group(1):
            continue  # вже прочитано (напр. власний лист, переміщений фільтром)
        msg = email.message_from_bytes(headers, policy=default_policy)
        name, addr = parseaddr(str(msg["From"] or ""))
        items.append({"uid": int(uid.group(1)), "from": (name or addr)[:100], "subject": str(msg["Subject"] or "")[:200]})
    return items[-MAX_PER_RUN:]


def check_mailbox(mailbox) -> int:
    from apps.mail import imap

    from .service import send_to_user

    key = _state_key(mailbox)
    state = cache.get(key)
    with imap.imap_session(mailbox.address) as conn:
        current = _status(conn)
        if current is None:
            return 0
        validity, uidnext = current
        if state is None or state[0] != validity or uidnext <= state[1]:
            cache.set(key, current, STATE_TTL)
            return 0
        first = max(state[1], uidnext - MAX_PER_RUN)
        items = _fetch_new(conn, first, uidnext - 1)
    cache.set(key, current, STATE_TTL)
    user = mailbox.user
    for item in items:
        with language_of(user):
            title = item["from"] or _("Новий лист")
            body = item["subject"] or _("(без теми)")
        send_to_user(user, "mail", title, body, "/mail", f"mail-{mailbox.pk.hex[:8]}-{item['uid']}", private=True)
    return len(items)


def poll() -> int:
    from apps.mail.models import Mailbox

    from .models import PushSubscription

    if not cache.add(LOCK_KEY, 1, 110):
        return 0  # попередній прохід ще триває
    try:
        user_ids = PushSubscription.objects.filter(notify_mail=True, user__is_active=True).values("user_id")
        mailboxes = Mailbox.objects.filter(user_id__in=user_ids, active=True, domain__active=True).select_related("domain", "user")
        total = 0
        for mailbox in mailboxes[:MAX_MAILBOXES]:
            try:
                total += check_mailbox(mailbox)
            except Exception as exc:  # Dovecot недоступний тощо — спробуємо наступного разу
                log.info("push mail poll %s: %s", mailbox.pk, exc.__class__.__name__)
        return total
    finally:
        cache.delete(LOCK_KEY)
