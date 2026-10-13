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
from datetime import timedelta
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import formataddr, getaddresses, make_msgid, parsedate_to_datetime

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from rest_framework.exceptions import APIException, NotFound, ValidationError

from .sanitize import sanitize_html

IMAP_TIMEOUT = 20
FETCH_FIELDS = "(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE CONTENT-TYPE)])"
SPECIAL_USE = ("\\Sent", "\\Drafts", "\\Trash", "\\Junk", "\\Archive")


class MailUnavailable(APIException):
    status_code = 503
    default_detail = gettext_lazy("Поштовий сервер тимчасово недоступний.")


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
        raise ValidationError({"folder": [_("Невірна назва теки.")]})
    return '"' + encoded.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _check_uid(uid) -> str:
    uid = str(uid)
    if not uid.isdigit() or len(uid) > 10:
        raise ValidationError({"uid": [_("Невірний UID.")]})
    return uid


# ─────────────────────── З'єднання ───────────────────────


@contextmanager
def imap_session(address: str):
    if not settings.DOVECOT_MASTER_PASSWORD:
        raise MailUnavailable(_("Веб-пошту не налаштовано (немає master-пароля Dovecot)."))
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
        raise NotFound(_("Теку не знайдено."))
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
                "subject": _decode_header(msg["Subject"]) or _("(без теми)"),
                "date": date,
                "size": int(size.group(1)) if size else 0,
                "seen": "\\Seen" in flag_list,
                "flagged": "\\Flagged" in flag_list,
                "answered": "\\Answered" in flag_list,
                "has_attachments": "multipart/mixed" in ctype,
            }
        )
    return items


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _search(conn, query: str) -> set[int]:
    """Пошук за темою або відправником (будь-якою мовою).

    Текст передається IMAP-літералом {n}: так він не може «вийти» з рядка й додати
    власні команди (CRLF-ін'єкція), а кирилиця передається як UTF-8."""
    q = _CONTROL.sub("", query).strip()[:100]
    if not q:
        return set()
    found: set[int] = set()
    for field in ("SUBJECT", "FROM"):
        conn.literal = q.encode("utf-8")
        typ, data = conn.uid("SEARCH", "CHARSET", "UTF-8", field)
        if typ == "OK":
            found.update(int(x) for x in (data[0] or b"").split())
    return found


def list_messages(address: str, folder: str, page: int = 1, page_size: int = 50, query: str = "") -> dict:
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    with imap_session(address) as conn:
        _select(conn, folder, readonly=True)
        if query:
            uids = sorted(_search(conn, query), reverse=True)
        else:
            typ, data = conn.uid("SEARCH", None, "ALL")
            uids = sorted((int(x) for x in (data[0] or b"").split()), reverse=True) if typ == "OK" else []
        total = len(uids)
        page_uids = uids[(page - 1) * page_size : page * page_size]
        items = []
        if page_uids:
            typ, fetched = conn.uid("FETCH", ",".join(map(str, page_uids)), FETCH_FIELDS)
            items = sorted(_parse_fetch(fetched), key=lambda m: m["uid"] or 0, reverse=True)
    for item in items:
        item["folder"] = folder
    return {"total": total, "page": page, "page_size": page_size, "results": items}


# ─────────────────────── «З зірочкою» (віртуальна тека) ───────────────────────

STARRED = "__starred__"
STARRED_SCAN_LIMIT = 1000
INTERNALDATE_RE = re.compile(rb'INTERNALDATE "[^"]+"')


def _selectable_folders(conn) -> list[tuple[str, str]]:
    """(назва, прапорці LIST) усіх тек, які можна відкрити."""
    typ, data = conn.list()
    result = []
    for raw in data or []:
        if not isinstance(raw, bytes):
            continue
        m = LIST_RE.match(raw)
        if not m:
            continue
        flags = m.group("flags").decode()
        if "\\Noselect" in flags or "\\NonExistent" in flags:
            continue
        name = m.group("name").decode().strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        result.append((decode_folder(name), flags))
    return result


def _internal_dates(conn, uids: list[int]) -> dict[int, float]:
    if not uids:
        return {}
    typ, data = conn.uid("FETCH", ",".join(map(str, uids)), "(UID INTERNALDATE)")
    result = {}
    for part in data or []:
        meta = part[0] if isinstance(part, tuple) else part
        if not isinstance(meta, bytes):
            continue
        uid = re.search(rb"UID (\d+)", meta)
        date = INTERNALDATE_RE.search(meta)
        if not uid:
            continue
        stamp = 0.0
        if date:
            parsed = imaplib.Internaldate2tuple(date.group(0))
            if parsed:
                import time

                stamp = time.mktime(parsed)
        result[int(uid.group(1))] = stamp
    return result


def list_starred(address: str, page: int = 1, page_size: int = 50, query: str = "") -> dict:
    """Листи з \\Flagged з усіх тек, крім «Кошика» і «Спаму» — новіші першими.

    Кожен елемент має справжню теку (folder): читання, дії й вкладення працюють із нею.
    Переглядається щонайбільше STARRED_SCAN_LIMIT листів (новіші UID кожної теки)."""
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    candidates: list[tuple[float, str, int]] = []
    truncated = False
    with imap_session(address) as conn:
        for name, flags in _selectable_folders(conn):
            if "\\Trash" in flags or "\\Junk" in flags:
                continue
            budget = STARRED_SCAN_LIMIT - len(candidates)
            if budget <= 0:
                truncated = True
                break
            try:
                _select(conn, name, readonly=True)
            except NotFound:
                continue
            typ, data = conn.uid("SEARCH", None, "FLAGGED")
            uids = {int(x) for x in (data[0] or b"").split()} if typ == "OK" and data else set()
            if uids and query:
                uids &= _search(conn, query)
            ordered = sorted(uids, reverse=True)
            if len(ordered) > budget:
                ordered, truncated = ordered[:budget], True
            dates = _internal_dates(conn, ordered)
            candidates.extend((dates.get(uid, 0.0), name, uid) for uid in ordered)
        candidates.sort(key=lambda c: (c[0], c[2]), reverse=True)
        total = len(candidates)
        chunk = candidates[(page - 1) * page_size : page * page_size]
        by_folder: dict[str, list[int]] = {}
        for _stamp, name, uid in chunk:
            by_folder.setdefault(name, []).append(uid)
        fetched: dict[tuple[str, int], dict] = {}
        for name, uids in by_folder.items():
            _select(conn, name, readonly=True)
            typ, data = conn.uid("FETCH", ",".join(map(str, uids)), FETCH_FIELDS)
            for item in _parse_fetch(data):
                item["folder"] = name
                fetched[(name, item["uid"])] = item
    items = [fetched[(name, uid)] for _stamp, name, uid in chunk if (name, uid) in fetched]
    return {"total": total, "page": page, "page_size": page_size, "results": items, "truncated": truncated}


# ─────────────────────── Один лист ───────────────────────


def _fetch_raw(conn, uid: str) -> bytes:
    typ, data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
    for part in data or []:
        if isinstance(part, tuple):
            return part[1]
    raise NotFound(_("Лист не знайдено."))


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


def _fetch_raw_flags(conn, uid: str) -> tuple[bytes, list[str]]:
    typ, data = conn.uid("FETCH", uid, "(FLAGS BODY.PEEK[])")
    for part in data or []:
        if isinstance(part, tuple):
            flags = re.search(rb"FLAGS \(([^)]*)\)", part[0])
            return part[1], (flags.group(1).decode().split() if flags else [])
    raise NotFound(_("Лист не знайдено."))


def get_message(address: str, folder: str, uid, *, mark_seen: bool = True) -> dict:
    uid = _check_uid(uid)
    with imap_session(address) as conn:
        _select(conn, folder, readonly=not mark_seen)
        raw, flags = _fetch_raw_flags(conn, uid)
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
        "flagged": "\\Flagged" in flags,
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
    raise NotFound(_("Вкладення не знайдено."))


# ─────────────────────── Дії ───────────────────────


def set_flag(address: str, folder: str, uid, flag: str, value: bool):
    uid = _check_uid(uid)
    if flag not in ("\\Seen", "\\Flagged"):
        raise ValidationError({"flag": [_("Невідомий прапорець.")]})
    with imap_session(address) as conn:
        _select(conn, folder, readonly=False)
        conn.uid("STORE", uid, "+FLAGS" if value else "-FLAGS", f"({flag})")


def move_message(address: str, folder: str, uid, target: str):
    uid = _check_uid(uid)
    with imap_session(address) as conn:
        _select(conn, folder, readonly=False)
        typ, _data = conn.uid("MOVE", uid, quote(target))
        if typ != "OK":
            raise ValidationError({"target": [_("Не вдалося перемістити лист.")]})


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


# ─────────────────────── Групові дії ───────────────────────

MAX_BULK = 500
BULK_ACTIONS = ("delete", "move", "seen", "unseen", "flag", "unflag", "spam", "notspam", "archive")


def _uid_set(uids) -> str:
    """Перевірений набір UID для однієї IMAP-команди («1,5,7»)."""
    if not isinstance(uids, (list, tuple)) or not uids:
        raise ValidationError({"uids": [_("Не вибрано жодного листа.")]})
    if len(uids) > MAX_BULK:
        raise ValidationError({"uids": [_("Максимум %(n)s листів за раз.") % {"n": MAX_BULK}]})
    clean = sorted({int(_check_uid(u)) for u in uids})
    if clean[0] <= 0:
        raise ValidationError({"uid": [_("Невірний UID.")]})
    return ",".join(map(str, clean))


def bulk_action(address: str, folder: str, uids, action: str, target: str | None = None) -> dict:
    """Одна дія над багатьма листами теки за одну IMAP-сесію (UID STORE / UID MOVE / UID EXPUNGE)."""
    if action not in BULK_ACTIONS:
        raise ValidationError({"action": [_("Невідома дія.")]})
    uid_set = _uid_set(uids)
    count = len(uid_set.split(","))
    flag_ops = {
        "seen": ("+FLAGS", "(\\Seen)"),
        "unseen": ("-FLAGS", "(\\Seen)"),
        "flag": ("+FLAGS", "(\\Flagged)"),
        "unflag": ("-FLAGS", "(\\Flagged)"),
    }
    with imap_session(address) as conn:
        if action in flag_ops:
            _select(conn, folder, readonly=False)
            typ, _data = conn.uid("STORE", uid_set, *flag_ops[action])
            if typ != "OK":
                raise MailUnavailable()
            return {"action": action, "count": count}
        if action == "delete":
            trash = _special_folder(conn, "\\Trash", "Trash")
            _select(conn, folder, readonly=False)
            if folder == trash:
                conn.uid("STORE", uid_set, "+FLAGS", "(\\Deleted)")
                typ, _data = conn.uid("EXPUNGE", uid_set)
                if typ != "OK":
                    raise MailUnavailable()
                return {"action": action, "count": count, "permanent": True}
            dest = trash
        elif action == "spam":
            dest = _special_folder(conn, "\\Junk", "Junk")
        elif action == "notspam":
            dest = "INBOX"
        elif action == "archive":
            dest = _special_folder(conn, "\\Archive", "")
            if not dest:
                raise ValidationError({"action": [_("Теки «Архів» немає.")]})
        else:
            dest = str(target or "")[:200]
            if not dest:
                raise ValidationError({"target": [_("Вкажіть теку.")]})
        if dest == folder:
            raise ValidationError({"target": [_("Листи вже в цій теці.")]})
        _select(conn, folder, readonly=False)
        typ, _data = conn.uid("MOVE", uid_set, quote(dest))
        if typ != "OK":
            raise ValidationError({"target": [_("Не вдалося перемістити листи.")]})
    return {"action": action, "count": count, "target": dest}


# ─────────────────────── Заблоковані відправники ───────────────────────

BLOCK_SCAN_LIMIT = 500
_LOCAL_RE = re.compile(r"[^\s@<>\"(),;:\\\[\]]{1,64}")
_DOMAIN_RE = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


def normalize_block_entry(value) -> str:
    """Адреса (bob@example.com, можна «Ім'я <bob@example.com>») або домен (@example.com).

    Повертає нормалізований запис у нижньому регістрі або кидає ValidationError."""
    from email.utils import parseaddr

    raw = str(value or "").strip()
    if not raw or len(raw) > 320 or any(c in raw for c in "\r\n\0"):
        raise ValidationError({"address": [_("Невірна адреса.")]})
    if raw.startswith("@"):
        domain = raw[1:].lower().rstrip(".")
        if not _DOMAIN_RE.fullmatch(domain):
            raise ValidationError({"address": [_("Невірний домен.")]})
        return "@" + domain
    addr = parseaddr(raw)[1] if "<" in raw else raw
    addr = addr.strip().lower()
    local, sep, domain = addr.rpartition("@")
    if not sep or not _LOCAL_RE.fullmatch(local) or not _DOMAIN_RE.fullmatch(domain.rstrip(".")):
        raise ValidationError({"address": [_("Невірна адреса.")]})
    return f"{local}@{domain.rstrip('.')}"


def sender_matches(from_header: str, entries) -> bool:
    """Чи відповідає адреса з From одному із записів (точна адреса або точний домен)."""
    from email.utils import parseaddr

    addr = parseaddr(str(from_header or ""))[1].strip().lower()
    if "@" not in addr:
        return False
    domain = "@" + addr.rpartition("@")[2].rstrip(".")
    return any(e == addr or e == domain for e in entries)


def _search_from(conn, needle: str) -> set[int]:
    """UID SEARCH FROM <літерал> — текст не може «вийти» з рядка (див. _search)."""
    conn.literal = needle.encode("utf-8")
    typ, data = conn.uid("SEARCH", "CHARSET", "UTF-8", "FROM")
    return {int(x) for x in (data[0] or b"").split()} if typ == "OK" and data else set()


def apply_blocklist(conn, entries, folder: str = "INBOX") -> int:
    """Переміщує в «Спам» листи заблокованих відправників із теки (за відкритої сесії).

    IMAP SEARCH FROM шукає підрядок, тому кожен збіг перевіряється за точною адресою з From."""
    entries = [e for e in entries if e]
    if not entries:
        return 0
    junk = _special_folder(conn, "\\Junk", "Junk")
    if folder == junk:
        return 0
    _select(conn, folder, readonly=False)
    hits: set[int] = set()
    for entry in entries:
        hits |= _search_from(conn, entry)
    if not hits:
        return 0
    candidates = sorted(hits, reverse=True)[:BLOCK_SCAN_LIMIT]
    typ, data = conn.uid("FETCH", ",".join(map(str, candidates)), "(UID BODY.PEEK[HEADER.FIELDS (FROM)])")
    matched = []
    for part in data or []:
        if not isinstance(part, tuple):
            continue
        uid = re.search(rb"UID (\d+)", part[0])
        msg = email.message_from_bytes(part[1], policy=default_policy)
        try:
            sender = str(msg["From"] or "")
        except Exception:
            continue
        if uid and int(uid.group(1)) in hits and sender_matches(sender, entries):
            matched.append(int(uid.group(1)))
    if not matched:
        return 0
    typ, _data = conn.uid("MOVE", ",".join(map(str, sorted(matched))), quote(junk))
    return len(matched) if typ == "OK" else 0


def apply_blocklist_for(address: str, entries) -> int:
    with imap_session(address) as conn:
        return apply_blocklist(conn, entries)


# ─────────────────────── Надсилання ───────────────────────

MAX_RECIPIENTS = 50


def _parse_recipients(value: str, field: str) -> list[str]:
    if not value:
        return []
    if any(c in value for c in "\r\n"):
        raise ValidationError({field: [_("Недопустимі символи.")]})
    pairs = getaddresses([value])
    result = []
    for name, addr in pairs:
        addr = addr.strip()
        if not re.fullmatch(r"[^@\s<>\"]+@[^@\s<>\"]+\.[^@\s<>\"]+", addr):
            raise ValidationError({field: [_("Невірна адреса: %(address)s") % {"address": addr or name}]})
        result.append(formataddr((name, addr)) if name else addr)
    return result


def _truthy(value) -> bool:
    return str(value).lower() in ("1", "true", "on", "yes")


def build_message(user, mailbox, data: dict, attachments: list, *, draft: bool = False) -> tuple[EmailMessage, list[str], dict]:
    """Збирає MIME-лист. Повертає (лист, усі адреси отримувачів, додаткові дані для відповіді).

    * html — вміст із редактора; санітизується на сервері (білий список тегів),
      вбудовані зображення стають cid:-частинами multipart/related;
    * body — текстова версія (якщо html немає, лист лише текстовий);
    * confidential — вміст лишається на сервері, отримувачі одержують посилання."""
    from . import confidential as conf
    from .sanitize import extract_data_images, html_to_text, sanitize_outgoing_html

    to = _parse_recipients(data.get("to", ""), "to")
    cc = _parse_recipients(data.get("cc", ""), "cc")
    bcc = _parse_recipients(data.get("bcc", ""), "bcc")
    if not to and not cc and not bcc and not draft:
        raise ValidationError({"to": [_("Вкажіть хоча б одного отримувача.")]})
    if len(to) + len(cc) + len(bcc) > MAX_RECIPIENTS:
        raise ValidationError({"to": [_("Максимум %(n)s отримувачів.") % {"n": MAX_RECIPIENTS}]})
    subject = (data.get("subject") or "").replace("\r", " ").replace("\n", " ")[:300]
    sender_name = mailbox.display_name or user.display_name or user.username

    raw_html = data.get("html") or ""
    if len(raw_html) > 20 * 1024 * 1024:
        raise ValidationError({"html": [_("Лист завеликий.")]})
    html = sanitize_outgoing_html(raw_html) if raw_html.strip() else ""
    text = data.get("body") or (html_to_text(html) if html else "")
    extra: dict = {}

    msg = EmailMessage()
    msg["From"] = Address(display_name=sender_name, addr_spec=mailbox.address)
    if to:
        msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if draft and bcc:
        # Лише в чернетці (вона лежить у власній скриньці); у надісланому листі Bcc немає
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    msg["Date"] = timezone.now()
    msg["Message-ID"] = make_msgid(domain=mailbox.domain.name)
    for header in ("in_reply_to", "references"):
        value = (data.get(header) or "").strip()
        if value and not any(c in value for c in "\r\n") and len(value) < 2000:
            msg["In-Reply-To" if header == "in_reply_to" else "References"] = value
    msg["User-Agent"] = "BlackCloud Webmail"

    if _truthy(data.get("confidential")) and not draft:
        if attachments:
            raise ValidationError({"attachments": [_("У конфіденційному режимі вкладення не підтримуються.")]})
        content = html or f"<pre>{escape_html(text)}</pre>"
        record, token, passcode = conf.create(
            sender=user,
            from_address=mailbox.address,
            recipients=[a for _name, a in getaddresses(to + cc + bcc)],
            subject=subject,
            html=content,
            days=data.get("confidential_days"),
            with_passcode=_truthy(data.get("confidential_passcode")),
        )
        notice_text, notice_html = conf.notice(record, token, sender_name)
        msg.set_content(notice_text)
        msg.add_alternative(notice_html, subtype="html")
        extra["confidential"] = {"id": str(record.pk), "expires_at": record.expires_at.isoformat(), "passcode": passcode}
    elif html:
        try:
            html_cid, images = extract_data_images(html, mailbox.domain.name)
        except ValueError as exc:
            raise ValidationError({"html": [_("Невірне або завелике вбудоване зображення.")]}) from exc
        total_images = sum(len(i["data"]) for i in images)
        if total_images > settings.MAIL_MAX_ATTACHMENTS_BYTES:
            raise ValidationError({"html": [_("Зображення в листі завеликі (максимум 15 МБ разом).")]})
        extra["inline_bytes"] = total_images
        msg.set_content(text or " ")
        msg.add_alternative(html_cid, subtype="html")
        html_part = msg.get_payload()[1]
        for img in images:
            html_part.add_related(img["data"], maintype=img["maintype"], subtype=img["subtype"], cid=f"<{img['cid']}>")
    else:
        msg.set_content(text)

    total = extra.get("inline_bytes", 0)
    for upload in attachments:
        total += upload.size
        if total > settings.MAIL_MAX_ATTACHMENTS_BYTES:
            raise ValidationError({"attachments": [_("Вкладення завеликі (максимум 15 МБ разом).")]})
        content = upload.read()
        maintype, _sep, subtype = (upload.content_type or "application/octet-stream").partition("/")
        msg.add_attachment(content, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=upload.name)
    return msg, [a for _name, a in getaddresses(to + cc + bcc)], extra


def escape_html(value: str) -> str:
    from html import escape

    return escape(value)


def _deliver(mailbox, raw: bytes, recipients: list[str], reply_uid=None, reply_folder=None) -> None:
    """SMTP через внутрішній Postfix + копія в «Надіслані» + позначка «Відповіли» на оригіналі."""
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as smtp:
            smtp.sendmail(mailbox.address, recipients, raw)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailUnavailable(_("Не вдалося надіслати: %(error)s") % {"error": exc.__class__.__name__}) from exc

    # Копія в «Надіслані» (Bcc не зберігаємо в заголовках листа)
    try:
        with imap_session(mailbox.address) as conn:
            sent = _special_folder(conn, "\\Sent", "Sent")
            conn.append(quote(sent), "(\\Seen)", imaplib.Time2Internaldate(timezone.now()), raw)
            if reply_uid and reply_folder:
                _select(conn, reply_folder, readonly=False)
                conn.uid("STORE", _check_uid(reply_uid), "+FLAGS", "(\\Answered)")
    except Exception:
        pass


def _forget_confidential(extra: dict) -> None:
    if "confidential" in extra:
        from .models import ConfidentialMessage

        ConfidentialMessage.objects.filter(pk=extra["confidential"]["id"]).delete()


def send_message(user, mailbox, data: dict, attachments: list) -> dict:
    msg, recipients, extra = build_message(user, mailbox, data, attachments)
    try:
        _deliver(mailbox, msg.as_bytes(), recipients, data.get("in_reply_to_uid"), data.get("in_reply_to_folder"))
    except MailUnavailable:
        _forget_confidential(extra)
        raise
    return {"message_id": str(msg["Message-ID"]), **{k: v for k, v in extra.items() if k == "confidential"}}


# ─────────────────────── Відкладене надсилання ───────────────────────

MAX_SCHEDULED_PER_USER = 100
SCHEDULE_MIN_DELAY = timedelta(minutes=1)
SCHEDULE_MAX_DELAY = timedelta(days=365)


def parse_send_at(value) -> "datetime":
    """ISO-дата з часовим поясом; щонайменше за хвилину від зараз і не далі ніж за рік."""
    from django.utils.dateparse import parse_datetime

    when = parse_datetime(str(value or "").strip())
    if when is None or timezone.is_naive(when):
        raise ValidationError({"send_at": [_("Невірний час надсилання.")]})
    now = timezone.now()
    if when < now + SCHEDULE_MIN_DELAY - timedelta(seconds=30):
        raise ValidationError({"send_at": [_("Час надсилання має бути в майбутньому.")]})
    if when > now + SCHEDULE_MAX_DELAY:
        raise ValidationError({"send_at": [_("Запланувати можна щонайбільше на рік уперед.")]})
    return when


def schedule_message(user, mailbox, data: dict, attachments: list, send_at) -> dict:
    """Збирає лист зараз (перевірки, санітизація, вкладення) і зберігає зашифрованим до send_at."""
    import json

    from apps.core.crypto import encrypt_str

    from .models import ConfidentialMessage, ScheduledMessage

    pending = ScheduledMessage.objects.filter(user=user, status=ScheduledMessage.Status.PENDING).count()
    if pending >= MAX_SCHEDULED_PER_USER:
        raise ValidationError({"send_at": [_("Забагато запланованих листів (максимум %(n)s).") % {"n": MAX_SCHEDULED_PER_USER}]})
    msg, recipients, extra = build_message(user, mailbox, data, attachments)
    conf = extra.get("confidential")
    if conf:
        # Термін доступу рахується від моменту фактичного надсилання
        record = ConfidentialMessage.objects.get(pk=conf["id"])
        record.expires_at += send_at - timezone.now()
        record.save(update_fields=["expires_at"])
        conf["expires_at"] = record.expires_at.isoformat()
    meta = {
        "to": str(msg["To"] or msg["Cc"] or ""),
        "subject": str(msg["Subject"] or ""),
        "recipients": recipients,
        "reply_uid": data.get("in_reply_to_uid") or None,
        "reply_folder": data.get("in_reply_to_folder") or None,
        "confidential_id": conf["id"] if conf else None,
    }
    item = ScheduledMessage.objects.create(
        user=user,
        mailbox=mailbox,
        send_at=send_at,
        payload_encrypted=encrypt_str(base64.b64encode(msg.as_bytes()).decode()),
        meta_encrypted=encrypt_str(json.dumps(meta)),
    )
    result = {"message_id": str(msg["Message-ID"]), "scheduled": {"id": str(item.pk), "send_at": send_at.isoformat()}}
    if conf:
        result["confidential"] = conf
    return result


def scheduled_meta(item) -> dict:
    import json

    from apps.core.crypto import decrypt_str

    try:
        return json.loads(decrypt_str(item.meta_encrypted)) if item.meta_encrypted else {}
    except Exception:
        return {}


def deliver_scheduled(item) -> None:
    """Надсилає запланований лист (виклик із Celery). Дата листа — момент фактичного надсилання."""
    from email.utils import format_datetime

    from apps.core.crypto import decrypt_str

    if not item.mailbox.active:
        raise MailUnavailable(_("Скриньку вимкнено."))
    meta = scheduled_meta(item)
    msg = email.message_from_bytes(base64.b64decode(decrypt_str(item.payload_encrypted)), policy=default_policy)
    del msg["Date"]
    msg["Date"] = format_datetime(timezone.now())
    _deliver(item.mailbox, msg.as_bytes(), meta.get("recipients") or [], meta.get("reply_uid"), meta.get("reply_folder"))


def cancel_scheduled(item) -> None:
    from .models import ConfidentialMessage, ScheduledMessage

    meta = scheduled_meta(item)
    if meta.get("confidential_id"):
        ConfidentialMessage.objects.filter(pk=meta["confidential_id"]).delete()
    item.status = ScheduledMessage.Status.CANCELLED
    item.payload_encrypted = ""
    item.save(update_fields=["status", "payload_encrypted"])


# ─────────────────────── Чернетки ───────────────────────

APPENDUID_RE = re.compile(rb"APPENDUID \d+ (\d+)")


class MemoryUpload:
    """Вкладення, взяте з чернетки (той самий інтерфейс, що й у завантаженого файлу)."""

    def __init__(self, name: str, content_type: str, data: bytes):
        self.name, self.content_type, self.data, self.size = name, content_type, data, len(data)

    def read(self) -> bytes:
        return self.data


def _drafts(conn) -> str:
    return _special_folder(conn, "\\Drafts", "Drafts")


def _expunge(conn, uid: str) -> None:
    conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
    conn.uid("EXPUNGE", uid)


def _fetch_draft(conn, uid) -> "EmailMessage":
    folder = _drafts(conn)
    _select(conn, folder)
    raw = _fetch_raw(conn, _check_uid(uid))
    return email.message_from_bytes(raw, policy=default_policy)


def draft_attachments(mailbox, uid, keep) -> list[MemoryUpload]:
    """Вкладення попередньої версії чернетки, які користувач залишив (за індексами частин)."""
    wanted = set()
    for value in keep or []:
        try:
            wanted.add(int(value))
        except (TypeError, ValueError):
            continue
    if not uid or not wanted:
        return []
    with imap_session(mailbox.address) as conn:
        msg = _fetch_draft(conn, uid)
    result = []
    for index, part in enumerate(msg.walk()):
        if index in wanted and not part.is_multipart():
            data = part.get_payload(decode=True) or b""
            result.append(MemoryUpload(part.get_filename() or f"attachment-{index}", part.get_content_type(), data))
    return result


def save_draft(user, mailbox, data: dict, attachments: list, replace_uid=None) -> dict:
    """Зберігає чернетку в теку «Чернетки» (як Gmail/Thunderbird) і прибирає попередню версію."""
    msg, _rcpts, _extra = build_message(user, mailbox, data, attachments, draft=True)
    raw = msg.as_bytes()
    with imap_session(mailbox.address) as conn:
        folder = _drafts(conn)
        typ, resp = conn.append(quote(folder), "(\\Draft \\Seen)", imaplib.Time2Internaldate(timezone.now()), raw)
        if typ != "OK":
            raise MailUnavailable()
        match = APPENDUID_RE.search(b" ".join(r for r in resp or [] if isinstance(r, bytes)))
        uid = int(match.group(1)) if match else None
        _select(conn, folder, readonly=False)
        if uid is None:
            typ, found = conn.uid("SEARCH", None, "HEADER", "Message-ID", f'"{msg["Message-ID"]}"')
            ids = (found[0] or b"").split() if typ == "OK" and found else []
            uid = int(ids[-1]) if ids else None
        if replace_uid and str(replace_uid) != str(uid):
            try:
                _expunge(conn, _check_uid(replace_uid))
            except Exception:
                pass
    return {"uid": uid, "attachments": _attachments(msg)}


def load_draft(mailbox, uid) -> dict:
    from .sanitize import sanitize_outgoing_html

    with imap_session(mailbox.address) as conn:
        msg = _fetch_draft(conn, uid)
    html_part = msg.get_body(preferencelist=("html",))
    text_part = msg.get_body(preferencelist=("plain",))
    if html_part is not None:
        html = html_part.get_content()
        for cid, data_uri in _inline_images(msg).items():
            html = html.replace(f"cid:{cid}", data_uri)
        html = sanitize_outgoing_html(html)
    else:
        text = text_part.get_content() if text_part is not None else ""
        html = "<p>" + escape_html(text).replace("\n", "<br>") + "</p>"
    return {
        "uid": int(_check_uid(uid)),
        "to": _decode_header(msg["To"]),
        "cc": _decode_header(msg["Cc"]),
        "bcc": _decode_header(msg["Bcc"]),
        "subject": _decode_header(msg["Subject"]),
        "in_reply_to": str(msg["In-Reply-To"] or ""),
        "references": str(msg["References"] or ""),
        "html": html,
        "attachments": _attachments(msg),
    }


def delete_draft(mailbox, uid) -> None:
    with imap_session(mailbox.address) as conn:
        folder = _drafts(conn)
        _select(conn, folder, readonly=False)
        _expunge(conn, _check_uid(uid))
