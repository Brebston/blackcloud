"""Інтеграція з ONLYOFFICE Document Server (перегляд і редагування Word/Excel/PowerPoint).

Схема:
  1. Браузер просить у Django конфіг редактора (/api/office/config/<id>/) — лише для
     користувача з доступом до файлу. Конфіг підписаний JWT спільним секретом.
  2. Document Server сам завантажує файл з Django за внутрішньою адресою
     (http://backend:8000/api/office/file/...) з короткоживучим підписаним токеном.
  3. Після редагування Document Server надсилає callback; Django перевіряє JWT,
     забирає результат лише з внутрішньої адреси Document Server (захист від SSRF)
     і зберігає нову зашифровану версію файлу, яка знову проходить антивірус.
"""

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request

from django.conf import settings
from django.core import signing

FILE_SALT = "bc-office-file"
CALLBACK_SALT = "bc-office-callback"
# Document Server забирає файл одразу після відкриття редактора, тож 5 хвилин вистачає;
# витік такого посилання (наприклад, із журналу) швидко стає марним.
CALLBACK_TOKEN_MAX_AGE = 7 * 24 * 3600

WORD = {"docx", "doc", "docm", "dotx", "odt", "rtf", "txt"}
CELL = {"xlsx", "xls", "xlsm", "ods", "csv"}
SLIDE = {"pptx", "ppt", "ppsx", "odp"}
# Формати, які редагуються без конвертації
EDITABLE = {"docx", "xlsx", "pptx", "odt", "ods", "odp", "txt", "csv", "rtf"}


def document_type(ext: str) -> str | None:
    if ext in WORD:
        return "word"
    if ext in CELL:
        return "cell"
    if ext in SLIDE:
        return "slide"
    return None


# ─── мінімальний JWT HS256 (без сторонніх залежностей) ───


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def jwt_encode(payload: dict) -> str:
    secret = (settings.OFFICE_JWT_SECRET or "").encode()
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    sig = _b64(hmac.new(secret, f"{header}.{body}".encode(), hashlib.sha256).digest())
    return f"{header}.{body}.{sig}"


class JWTError(Exception):
    pass


def jwt_decode(token: str) -> dict:
    secret = (settings.OFFICE_JWT_SECRET or "").encode()
    if not secret:
        raise JWTError("secret not configured")
    try:
        header_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(_unb64(header_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise JWTError("malformed token") from exc
    if header.get("alg") != "HS256":
        raise JWTError("unexpected alg")
    expected = hmac.new(secret, f"{header_b64}.{body_b64}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _unb64(sig_b64)):
        raise JWTError("bad signature")
    payload = json.loads(_unb64(body_b64))
    if "exp" in payload and payload["exp"] < time.time():
        raise JWTError("expired")
    return payload


# ─── токени для внутрішніх запитів Document Server ───


def file_token(f) -> str:
    return signing.dumps({"f": str(f.id), "v": f.content_version}, salt=FILE_SALT)


def read_file_token(token: str) -> dict:
    return signing.loads(token, salt=FILE_SALT, max_age=settings.OFFICE_FILE_TOKEN_TTL)


def callback_token(f, user) -> str:
    return signing.dumps({"f": str(f.id), "u": str(user.pk)}, salt=CALLBACK_SALT)


def read_callback_token(token: str) -> dict:
    return signing.loads(token, salt=CALLBACK_SALT, max_age=CALLBACK_TOKEN_MAX_AGE)


def document_key(f) -> str:
    """Ключ документа для спільного редагування: змінюється з кожною новою версією."""
    raw = f"{f.id}:{f.content_version}:{settings.SECRET_KEY}".encode()
    return hashlib.sha256(raw).hexdigest()[:40]


def build_config(f, user, *, can_edit: bool, language: str = "uk", theme: str = "theme-dark") -> dict:
    ext = f.extension
    backend = settings.OFFICE_BACKEND_URL.rstrip("/")
    config = {
        "document": {
            "fileType": ext,
            "key": document_key(f),
            "title": f.name,
            "url": f"{backend}/api/office/file/{f.id}/?t={file_token(f)}",
            "permissions": {
                "edit": can_edit,
                "download": True,
                "print": True,
                "comment": can_edit,
                "review": can_edit,
                "fillForms": can_edit,
            },
        },
        "documentType": document_type(ext),
        "editorConfig": {
            "mode": "edit" if can_edit else "view",
            "lang": language,
            "user": {"id": str(user.pk), "name": user.display_name or user.username},
            "customization": {
                "forcesave": True,
                "autosave": True,
                "uiTheme": theme,
                "goback": False,
                "help": False,
                "feedback": False,
                # Макроси (JavaScript у документі) і плагіни вимкнено
                "macros": False,
                "plugins": False,
            },
        },
        "exp": int(time.time()) + settings.OFFICE_EDITOR_TOKEN_TTL,
    }
    if can_edit:
        config["editorConfig"]["callbackUrl"] = f"{backend}/api/office/callback/{f.id}/?t={callback_token(f, user)}"
    config["token"] = jwt_encode(config)
    return config


def fetch_result(url: str) -> bytes:
    """Завантажує відредагований файл ЛИШЕ з внутрішнього Document Server.

    Хост з callback ігнорується (його контролює клієнтська сторона) — береться тільки шлях.
    """
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path
    if not path.startswith("/") or ".." in path or "\\" in path:
        raise ValueError("unexpected path")
    internal = settings.OFFICE_INTERNAL_URL.rstrip("/") + path + (f"?{parsed.query}" if parsed.query else "")
    request = urllib.request.Request(internal, headers={"User-Agent": "BlackCloud"})
    with urllib.request.urlopen(request, timeout=60) as resp:  # noqa: S310 — адреса фіксована
        data = resp.read(settings.OFFICE_MAX_BYTES + 1)
    if len(data) > settings.OFFICE_MAX_BYTES:
        raise ValueError("result too large")
    return data
