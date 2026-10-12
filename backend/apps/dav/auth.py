"""HTTP Basic для WebDAV: лише паролі застосунків (DavPassword), лише HTTPS.

* Основний пароль акаунта НЕ приймається (у акаунтах є 2FA).
* Сесійні cookie ігноруються: автентифікація лише за заголовком Authorization,
  тож сторонній сайт не може скористатися сесією браузера (CSRF).
* Блокування після невдалих спроб — за іменем користувача і за IP, окремо від
  входу у вебінтерфейс (підбір пароля WebDAV не блокує вебвхід і навпаки).
* Argon2 повільний, а Finder робить сотні запитів: після успішної перевірки
  результат кешується за HMAC(SECRET_KEY, логін+пароль) на кілька хвилин.
  Кеш не дійсний, якщо пароль відкликано або акаунт вимкнено.
"""

import base64
import binascii
import hashlib
import hmac
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.utils import timezone

from apps.core.audit import audit, client_ip

from .models import DavPassword

User = get_user_model()

AUTH_CACHE_TTL = 300
LAST_USED_UPDATE_EVERY = 300
MAX_PASSWORDS = 10
ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_password() -> str:
    """6 груп по 5 символів з 55-символьного алфавіту: ~173 біти ентропії."""
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(5)) for _ in range(6))


def hash_password(raw: str) -> str:
    return make_password(raw)


# ─────────────────────────── Блокування ───────────────────────────


def _lock_key(kind: str, value: str) -> str:
    return f"dav-lockout:{kind}:{hashlib.sha256(value.lower().encode()).hexdigest()[:32]}"


def is_locked(username: str, ip: str | None) -> bool:
    limit = settings.LOGIN_MAX_FAILURES
    if (cache.get(_lock_key("user", username)) or 0) >= limit:
        return True
    return bool(ip) and (cache.get(_lock_key("ip", ip)) or 0) >= limit * 4


def _incr(key: str) -> int:
    ttl = settings.LOGIN_LOCKOUT_SECONDS
    if cache.add(key, 1, ttl):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, ttl)
        return 1


def register_failure(username: str, ip: str | None) -> int:
    count = _incr(_lock_key("user", username))
    if ip:
        _incr(_lock_key("ip", ip))
    return count


# ─────────────────────────── Перевірка ───────────────────────────


def _auth_cache_key(username: str, password: str) -> str:
    mac = hmac.new(settings.SECRET_KEY.encode(), f"{username}\0{password}".encode(), hashlib.sha256).hexdigest()
    return f"dav-auth:{mac}"


def parse_basic(header: str) -> tuple[str, str] | None:
    scheme, _, value = (header or "").partition(" ")
    if scheme.lower() != "basic" or not value or len(value) > 1024:
        return None
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    username, sep, password = decoded.partition(":")
    if not sep:
        return None
    return username.strip().lower(), password


class AuthResult:
    def __init__(self, user=None, credential=None, locked=False):
        self.user = user
        self.credential = credential
        self.locked = locked


def authenticate(request) -> AuthResult:
    """Повертає AuthResult; user=None означає 401 (або 429/401 при блокуванні)."""
    creds = parse_basic(request.META.get("HTTP_AUTHORIZATION", ""))
    if creds is None:
        return AuthResult()
    username, password = creds
    if not username or not password or len(username) > 254 or len(password) > 256:
        return AuthResult()
    ip = client_ip(request)

    ckey = _auth_cache_key(username, password)
    cached = cache.get(ckey)
    if cached:
        cred = DavPassword.objects.select_related("user").filter(pk=cached, user__is_active=True).first()
        if cred is not None and cred.user.username == username:
            _touch(request, cred, ip)
            return AuthResult(cred.user, cred)
        cache.delete(ckey)

    if is_locked(username, ip):
        return AuthResult(locked=True)

    user = User.objects.filter(username=username, is_active=True).first()
    match = None
    if user is not None:
        for cred in DavPassword.objects.filter(user=user)[:MAX_PASSWORDS]:
            if check_password(password, cred.password_hash):
                match = cred
                break
    else:
        # Вирівнюємо час відповіді для неіснуючих користувачів
        make_password(password)
    if match is None:
        count = register_failure(username, ip)
        audit(request, "dav.auth_failed", user=user, login=username[:64])
        if count == settings.LOGIN_MAX_FAILURES and user is not None:
            audit(request, "dav.locked", user=user)
        return AuthResult()

    cache.set(ckey, str(match.pk), AUTH_CACHE_TTL)
    match.user = user
    _touch(request, match, ip)
    return AuthResult(user, match)


def _touch(request, cred: DavPassword, ip: str | None) -> None:
    """last_used_at/ip — не частіше ніж раз на 5 хвилин; перше використання — в аудит."""
    first = cred.last_used_at is None
    if not first and not cache.add(f"dav-touch:{cred.pk}", 1, LAST_USED_UPDATE_EVERY):
        return
    updated = DavPassword.objects.filter(pk=cred.pk).update(last_used_at=timezone.now(), last_used_ip=ip)
    if first and updated:
        cred.last_used_at = timezone.now()
        audit(request, "dav.password_first_use", user=cred.user, target=str(cred.pk), label=cred.label)

