"""Web Push без сторонніх бібліотек: шифрування повідомлення (RFC 8291, кодування
aes128gcm з RFC 8188) і автентифікація сервера застосунку VAPID (RFC 8292).

Push-сервіс браузера (FCM, Mozilla, Apple, Microsoft) бачить лише шифротекст:
ключ розшифрування є тільки в браузері, що підписався.
"""

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from functools import lru_cache
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

RECORD_SIZE = 4096
SEND_TIMEOUT = 10
# Відомі push-сервіси браузерів. Інші адреси відхиляються: інакше сервер можна було б
# змусити надсилати POST-запити на довільні хости (SSRF).
ALLOWED_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "web.push.apple.com")
ALLOWED_SUFFIXES = (".push.services.mozilla.com", ".notify.windows.com", ".push.apple.com")


class WebPushError(Exception):
    pass


# ─────────────────────── base64url ───────────────────────


def b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(value: str) -> bytes:
    value = value.strip().replace("+", "-").replace("/", "_").rstrip("=")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


# ─────────────────────── Перевірка підписки ───────────────────────


def endpoint_allowed(endpoint: str) -> bool:
    """https, стандартний порт, без облікових даних у URL і лише відомі push-сервіси."""
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443):
        return False
    if any(c in endpoint for c in "\r\n\t "):
        return False
    return host in ALLOWED_HOSTS or any(host.endswith(s) and len(host) > len(s) for s in ALLOWED_SUFFIXES)


def load_public_point(p256dh: str) -> bytes:
    """Публічний ключ браузера (65 байтів, точка P-256 без стиснення) з перевіркою, що він на кривій."""
    raw = b64u_decode(p256dh)
    if len(raw) != 65 or raw[0] != 4:
        raise ValueError("bad p256dh")
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    return raw


# ─────────────────────── RFC 8291 ───────────────────────


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    # Довжина ≤ 32 байти — достатньо одного блоку HKDF-Expand
    return _hmac(_hmac(salt, ikm), info + b"\x01")[:length]


def derive(ua_public: bytes, auth_secret: bytes, salt: bytes, as_private: ec.EllipticCurvePrivateKey) -> dict:
    """Проміжні значення RFC 8291 (окремо — щоб перевіряти тестовими векторами)."""
    as_public = as_private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = as_private.exchange(ec.ECDH(), ua_key)
    key_info = b"WebPush: info\x00" + ua_public + as_public
    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, key_info + b"\x01")
    prk = _hmac(salt, ikm)
    return {
        "as_public": as_public,
        "shared": shared,
        "prk_key": prk_key,
        "ikm": ikm,
        "prk": prk,
        "cek": _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16],
        "nonce": _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12],
    }


def encrypt(plaintext: bytes, ua_public: bytes, auth_secret: bytes, *, salt: bytes | None = None, as_private=None) -> bytes:
    """Один запис aes128gcm: заголовок (salt | rs | idlen | keyid) + шифротекст.
    salt і as_private задаються лише в тестах — у роботі щоразу нові випадкові."""
    if len(auth_secret) != 16:
        raise ValueError("bad auth secret")
    if len(plaintext) > RECORD_SIZE - 86 - 17:
        raise ValueError("payload too large")
    salt = salt if salt is not None else os.urandom(16)
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    k = derive(ua_public, auth_secret, salt, as_private)
    ciphertext = AESGCM(k["cek"]).encrypt(k["nonce"], plaintext + b"\x02", None)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(k["as_public"])]) + k["as_public"]
    return header + ciphertext


# ─────────────────────── VAPID (RFC 8292) ───────────────────────


def private_key_from_pem(pem: str) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise WebPushError("VAPID key must be EC P-256")
    return key


def public_key_b64(key) -> str:
    """Публічний ключ VAPID у форматі для applicationServerKey (точка без стиснення, base64url)."""
    if isinstance(key, ec.EllipticCurvePrivateKey):
        key = key.public_key()
    return b64u_encode(key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))


def normalize_public_key(value: str) -> str:
    """Публічний ключ із секрету: PEM (як генерує openssl) або вже готовий base64url."""
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("-----BEGIN"):
        key = serialization.load_pem_public_key(value.encode())
        return public_key_b64(key)
    load_public_point(value)
    return b64u_encode(b64u_decode(value))


def vapid_jwt(audience: str, subject: str, key: ec.EllipticCurvePrivateKey, *, now: float | None = None, ttl: int = 12 * 3600) -> str:
    header = b64u_encode(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = {"aud": audience, "exp": int((now or time.time()) + ttl), "sub": subject}
    body = b64u_encode(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode("ascii")
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    return f"{header}.{body}.{b64u_encode(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


@lru_cache(maxsize=1)
def _vapid_key(pem: str):
    return private_key_from_pem(pem)


# ─────────────────────── Надсилання ───────────────────────


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # push-сервіс не має перенаправляти; не йдемо за ним
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def send(endpoint: str, p256dh: str, auth: str, payload: bytes, *, vapid_pem: str, subject: str, ttl: int = 86400, urgency: str = "normal") -> int:
    """Надсилає одне повідомлення. Повертає HTTP-статус push-сервісу (0 — мережева помилка)."""
    if not endpoint_allowed(endpoint):
        raise WebPushError("endpoint not allowed")
    key = _vapid_key(vapid_pem)
    body = encrypt(payload, load_public_point(p256dh), b64u_decode(auth))
    parts = urlsplit(endpoint)
    jwt = vapid_jwt(f"{parts.scheme}://{parts.netloc}", subject, key)
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Encoding": "aes128gcm",
            "TTL": str(int(ttl)),
            "Urgency": urgency,
            "Authorization": f"vapid t={jwt}, k={public_key_b64(key)}",
        },
    )
    try:
        with _opener.open(request, timeout=SEND_TIMEOUT) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError):
        return 0
