"""Блокування після серії невдалих спроб входу (за логіном і за IP)."""

import hashlib

from django.conf import settings
from django.core.cache import cache


def _key(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.lower().encode()).hexdigest()[:32]
    return f"lockout:{kind}:{digest}"


def is_locked(login: str, ip: str | None) -> bool:
    limit = settings.LOGIN_MAX_FAILURES
    if (cache.get(_key("login", login)) or 0) >= limit:
        return True
    # Для IP ліміт вищий: за одним NAT може бути багато людей
    if ip and (cache.get(_key("ip", ip)) or 0) >= limit * 4:
        return True
    return False


def _incr(key: str) -> int:
    ttl = settings.LOGIN_LOCKOUT_SECONDS
    if cache.add(key, 1, ttl):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, ttl)
        return 1


def register_failure(login: str, ip: str | None) -> int:
    count = _incr(_key("login", login))
    if ip:
        _incr(_key("ip", ip))
    return count


def reset(login: str) -> None:
    cache.delete(_key("login", login))
