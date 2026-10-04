"""Клієнт веб-пошти поверх IMAP (Dovecot) та SMTP (Postfix).

Django підключається до Dovecot як master-user ("<адреса>*webmail"), тому
пароль користувача для веб-пошти не потрібен і ніде не зберігається.
IMAP-порт 143 доступний лише у внутрішній Docker-мережі.
"""

import base64
import email
import imaplib
import re
import smtplib
from contextlib import contextmanager
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import formataddr, getaddresses, make_msgid, parsedate_to_datetime

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, ValidationError

from .sanitize import sanitize_html

IMAP_TIMEOUT = 20
FETCH_FIELDS = "(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE CONTENT-TYPE)])"
SPECIAL_USE = ("\\Sent", "\\Drafts", "\\Trash", "\\Junk", "\\Archive")


class MailUnavailable(APIException):
    status_code = 503
    default_detail = "Поштовий сервер тимчасово недоступний."


# ─────────────────────── Modified UTF-7 (RFC 3501) ───────────────────────


def _b64_mod_encode(s: str) -> str:
    return base64.b64encode(s.encode("utf-16-be")).decode().rstrip("=").replace("/", ",")


def encode_folder(name: str) -> str:
    out, buf = [], []
    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            if buf:
                out.append("&" + _b64_mod_encode("".join(buf)) + "-")
                buf = []
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    if buf:
        out.append("&" + _b64_mod_encode("".join(buf)) + "-")
    return "".join(out)


def decode_folder(name: str) -> str:
    def repl(m):
        chunk = m.group(1)
        if chunk == "":
            return "&"
        chunk = chunk.replace(",", "/")
        chunk += "=" * (-len(chunk) % 4)
        return base64.b64decode(chunk).decode("utf-16-be")

    return re.sub(r"&([^-]*)-", repl, name)


def quote(name: str) -> str:
    encoded = encode_folder(name)
    if any(c in encoded for c in "\r\n\0"):
        raise ValidationError({"folder": ["Невірна назва теки."]})
    return '"' + encoded.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _check_uid(uid) -> str:
    uid = str(uid)
    if not uid.isdigit() or len(uid) > 10:
        raise ValidationError({"uid": ["Невірний UID."]})
    return uid


# ─────────────────────── З'єднання ───────────────────────


@contextmanager
def imap_session(address: str):
    if not settings.DOVECOT_MASTER_PASSWORD:
        raise MailUnavailable("Веб-пошту не налаштовано (немає master-пароля Dovecot).")
    try:
        conn = imaplib.IMAP4(settings.IMAP_HOST, settings.IMAP_PORT, timeout=IMAP_TIMEOUT)
        conn.login(f"{address}*{settings.DOVECOT_MASTER_USER}", settings.DOVECOT_MASTER_PASSWORD)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise MailUnavailable() from exc
    try:
        yield conn
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _select(conn, folder: str, readonly: bool = True) -> int:
    typ, data = conn.select(quote(folder), readonly=readonly)
    if typ != "OK":
        raise NotFound("Теку не знайдено.")
    try:
        return int(data[0])
    except (TypeError, ValueError):
        return 0


# ─────────────────────── Теки ───────────────────────

LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\) (?P<delim>"[^"]*"|NIL) (?P<name>.+)$')


def list_folders(address: str) -> list[dict]:
    with imap_session(address) as conn:
        typ, data = conn.list()
        folders = []
        for raw in data or []:
            if not isinstance(raw, bytes):
                continue
            m = LIST_RE.match(raw)
            if not m:
                continue
            flags = m.group("flags").decode()
            name = m.group("name").decode().strip()
            if name.startswith('"') and name.endswith('"'):
                name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            if "\\Noselect" in flags:
                continue
            decoded = decode_folder(name)
            special = next((f for f in SPECIAL_USE if f in flags), None)
            if decoded.upper() == "INBOX":
                special = "\\Inbox"
            typ, st = conn.status(quote(decoded), "(MESSAGES UNSEEN)")
            messages = unseen = 0
            if typ == "OK" and st and st[0]:
                mm = re.search(rb"MESSAGES (\d+)", st[0])
                mu = re.search(rb"UNSEEN (\d+)", st[0])
                messages = int(mm.group(1)) if mm else 0
                unseen = int(mu.group(1)) if mu else 0
            folders.append({"name": decoded, "special": special, "messages": messages, "unseen": unseen})
    order = {"\\Inbox": 0, "\\Drafts": 1, "\\Sent": 2, "\\Archive": 3, "\\Junk": 4, "\\Trash": 5}
    folders.sort(key=lambda f: (order.get(f["special"], 10), f["name"].lower()))
    return folders


def _special_folder(conn, special: str, fallback: str) -> str:
    typ, data = conn.list()
    for raw in data or []:
        if isinstance(raw, bytes) and special.encode() in raw:
            m = LIST_RE.match(raw)
            if m:
                return decode_folder(m.group("name").decode().strip().strip('"'))
    return fallback


# ─────────────────────── Список листів ───────────────────────


def _decode_header(value) -> str:
    if value is None:
        return ""
    return str(value)


def _parse_fetch(data) -> list[dict]:
    items = []
    for part in data or []:
        if not isinstance(part, tuple):
            continue
        meta, header_bytes = part[0], part[1]
        uid = re.search(rb"UID (\d+)", meta)
        flags = re.search(rb"FLAGS \(([^)]*)\)", meta)
        size = re.search(rb"RFC822\.SIZE (\d+)", meta)
        msg = email.message_from_bytes(header_bytes, policy=default_policy)
        flag_list = flags.group(1).decode().split() if flags else []
        try:
            date = parsedate_to_datetime(msg["Date"]).isoformat() if msg["Date"] else None
        except (TypeError, ValueError):
            date = None
        ctype = (msg.get("Content-Type") or "").lower()
        items.append(
            {
                "uid": int(uid.group(1)) if uid else None,
                "from": _decode_header(msg["From"]),
                "to": _decode_header(msg["To"]),
                "subject": _decode_header(msg["Subject"]) or "(без теми)",
                "date": date,
                "size": int(size.group(1)) if size else 0,
                "seen": "\\Seen" in flag_list,
                "flagged": "\\Flagged" in flag_list,
                "answered": "\\Answered" in flag_list,
                "has_attachments": "multipart/mixed" in ctype,
            }
        )
    return items


def list_messages(address: str, folder: str, page: int = 1, page_size: int = 50, query: str = "") -> dict:
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    with imap_session(address) as conn:
        _select(conn, folder, readonly=True)
        if query:
            q = query.replace("\\", "").replace('"', "")[:100]
            typ, data = conn.uid("SEARCH", "CHARSET", "UTF-8", "OR", "SUBJECT", f'"{q}"', "FROM", f'"{q}"')
        else:
            typ, data = conn.uid("SEARCH", None, "ALL")
        uids = sorted((int(x) for x in (data[0] or b"").split()), reverse=True) if typ == "OK" else []
        total = len(uids)
        page_uids = uids[(page - 1) * page_size : page * page_size]
        items = []
        if page_uids:
            typ, fetched = conn.uid("FETCH", ",".join(map(str, page_uids)), FETCH_FIELDS)
            items = sorted(_parse_fetch(fetched), key=lambda m: m["uid"] or 0, reverse=True)
    return {"total": total, "page": page, "page_size": page_size, "results": items}


# ─────────────────────── Один лист ───────────────────────


def _fetch_raw(conn, uid: str) -> bytes:
    typ, data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
    for part in data or []:
        if isinstance(part, tuple):
            return part[1]
    raise NotFound("Лист не знайдено.")


def _inline_images(msg) -> dict[str, str]:
    images = {}
    for part in msg.walk():
        cid = part.get("Content-ID")
        if cid and part.get_content_maintype() == "image":
            payload = part.get_payload(decode=True) or b""
            if len(payload) <= 2 * 1024 * 1024:
                ctype = part.get_content_type()
                if ctype in ("image/png", "image/jpeg", "image/gif", "image/webp"):
                    images[cid.strip("<>")] = f"data:{ctype};base64," + base64.b64encode(payload).decode()
    return images


def _attachments(msg) -> list[dict]:
    result = []
    for index, part in enumerate(msg.walk()):
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        if disposition == "attachment" or (filename and disposition != "inline"):
            payload = part.get_payload(decode=True) or b""
            result.append(
                {"index": index, "filename": filename or f"attachment-{index}", "content_type": part.get_content_type(), "size": len(payload)}
            )
    return result


def get_message(address: str, folder: str, uid, *, mark_seen: bool = True) -> dict:
    uid = _check_uid(uid)
    with imap_session(address) as conn:
        _select(conn, folder, readonly=not mark_seen)
        raw = _fetch_raw(conn, uid)
        if mark_seen:
            conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
    msg = email.message_from_bytes(raw, policy=default_policy)
    text_part = msg.get_body(preferencelist=("plain",))
    html_part = msg.get_body(preferencelist=("html",))
    text = text_part.get_content() if text_part else ""
    try:
        date = parsedate_to_datetime(msg["Date"]).isoformat() if msg["Date"] else None
    except (TypeError, ValueError):
        date = None
    return {
        "uid": int(uid),
        "folder": folder,
        "from": str(msg["From"] or ""),
        "to": str(msg["To"] or ""),
        "cc": str(msg["Cc"] or ""),
        "reply_to": str(msg["Reply-To"] or ""),
        "subject": str(msg["Subject"] or ""),
        "date": date,
        "message_id": str(msg["Message-ID"] or ""),
        "references": str(msg["References"] or ""),
        "text": text[:500_000],
        "has_html": html_part is not None,
        "attachments": _attachments(msg),
    }


def render_html(address: str, folder: str, uid, *, allow_remote: bool) -> str:
    uid = _check_uid(uid)
    with imap_session(address) as conn:
        _select(conn, folder, readonly=True)
        raw = _fetch_raw(conn, uid)
    msg = email.message_from_bytes(raw, policy=default_policy)
    html_part = msg.get_body(preferencelist=("html",))
    if html_part is None:
        return ""
    return sanitize_html(html_part.get_content(), inline_images=_inline_images(msg), allow_remote=allow_remote)


def get_attachment(address: str, folder: str, uid, index: int) -> tuple[str, str, bytes]:
    uid = _check_uid(uid)
    with imap_session(address) as conn:
        _select(conn, folder, readonly=True)
        raw = _fetch_raw(conn, uid)
    msg = email.message_from_bytes(raw, policy=default_policy)
    for i, part in enumerate(msg.walk()):
        if i == index and not part.is_multipart():
            return part.get_filename() or f"attachment-{index}", part.get_content_type(), part.get_payload(decode=True) or b""
    raise NotFound("Вкладення не знайдено.")


# ─────────────────────── Дії ───────────────────────


def set_flag(address: str, folder: str, uid, flag: str, value: bool):
    uid = _check_uid(uid)
    if flag not in ("\\Seen", "\\Flagged"):
        raise ValidationError({"flag": ["Невідомий прапорець."]})
    with imap_session(address) as conn:
        _select(conn, folder, readonly=False)
        conn.uid("STORE", uid, "+FLAGS" if value else "-FLAGS", f"({flag})")


def move_message(address: str, folder: str, uid, target: str):
    uid = _check_uid(uid)
    with imap_session(address) as conn:
        _select(conn, folder, readonly=False)
        typ, _ = conn.uid("MOVE", uid, quote(target))
        if typ != "OK":
            raise ValidationError({"target": ["Не вдалося перемістити лист."]})


def delete_message(address: str, folder: str, uid):
    uid = _check_uid(uid)
    with imap_session(address) as conn:
        trash = _special_folder(conn, "\\Trash", "Trash")
        _select(conn, folder, readonly=False)
        if folder == trash:
            conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
            conn.uid("EXPUNGE", uid)
        else:
            conn.uid("MOVE", uid, quote(trash))


# ─────────────────────── Надсилання ───────────────────────

MAX_RECIPIENTS = 50


def _parse_recipients(value: str, field: str) -> list[str]:
    if not value:
        return []
    if any(c in value for c in "\r\n"):
        raise ValidationError({field: ["Недопустимі символи."]})
    pairs = getaddresses([value])
    result = []
    for name, addr in pairs:
        addr = addr.strip()
        if not re.fullmatch(r"[^@\s<>\"]+@[^@\s<>\"]+\.[^@\s<>\"]+", addr):
            raise ValidationError({field: [f"Невірна адреса: {addr or name}"]})
        result.append(formataddr((name, addr)) if name else addr)
    return result


def send_message(user, mailbox, data: dict, attachments: list) -> str:
    to = _parse_recipients(data.get("to", ""), "to")
    cc = _parse_recipients(data.get("cc", ""), "cc")
    bcc = _parse_recipients(data.get("bcc", ""), "bcc")
    if not to and not cc and not bcc:
        raise ValidationError({"to": ["Вкажіть хоча б одного отримувача."]})
    if len(to) + len(cc) + len(bcc) > MAX_RECIPIENTS:
        raise ValidationError({"to": [f"Максимум {MAX_RECIPIENTS} отримувачів."]})
    subject = (data.get("subject") or "").replace("\r", " ").replace("\n", " ")[:300]

    msg = EmailMessage()
    msg["From"] = Address(display_name=user.display_name or user.username, addr_spec=mailbox.address)
    if to:
        msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg["Date"] = timezone.now()
    msg["Message-ID"] = make_msgid(domain=mailbox.domain.name)
    for header in ("in_reply_to", "references"):
        value = (data.get(header) or "").strip()
        if value and not any(c in value for c in "\r\n") and len(value) < 2000:
            msg["In-Reply-To" if header == "in_reply_to" else "References"] = value
    msg["User-Agent"] = "BlackCloud Webmail"
    msg.set_content(data.get("body") or "")
    total = 0
    for upload in attachments:
        total += upload.size
        if total > settings.MAIL_MAX_ATTACHMENTS_BYTES:
            raise ValidationError({"attachments": ["Вкладення завеликі (максимум 15 МБ разом)."]})
        content = upload.read()
        maintype, _, subtype = (upload.content_type or "application/octet-stream").partition("/")
        msg.add_attachment(content, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=upload.name)

    recipients = [a for _, a in getaddresses(to + cc + bcc)]
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as smtp:
            smtp.send_message(msg, from_addr=mailbox.address, to_addrs=recipients)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailUnavailable(f"Не вдалося надіслати: {exc.__class__.__name__}") from exc

    # Копія в «Надіслані» (Bcc не зберігаємо в заголовках листа)
    try:
        with imap_session(mailbox.address) as conn:
            sent = _special_folder(conn, "\\Sent", "Sent")
            conn.append(quote(sent), "(\\Seen)", imaplib.Time2Internaldate(timezone.now()), msg.as_bytes())
            if data.get("in_reply_to_uid") and data.get("in_reply_to_folder"):
                _select(conn, data["in_reply_to_folder"], readonly=False)
                conn.uid("STORE", _check_uid(data["in_reply_to_uid"]), "+FLAGS", "(\\Answered)")
    except Exception:
        pass
    return str(msg["Message-ID"])
