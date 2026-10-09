"""Короткоживучі квитки в кеші (у кеші зберігається лише хеш квитка)."""

import hashlib
import secrets

from django.core.cache import cache


def _key(kind: str, ticket: str) -> str:
    return f"ticket:{kind}:" + hashlib.sha256(ticket.encode()).hexdigest()


def issue_ticket(kind: str, data: dict, ttl: int) -> str:
    ticket = secrets.token_urlsafe(32)
    cache.set(_key(kind, ticket), data, ttl)
    return ticket


def peek_ticket(kind: str, ticket: str) -> dict | None:
    if not ticket or len(ticket) > 100:
        return None
    return cache.get(_key(kind, ticket))


def consume_ticket(kind: str, ticket: str) -> dict | None:
    """Одноразовий квиток: delete() повертає True лише першому з паралельних запитів."""
    data = peek_ticket(kind, ticket)
    if data is None or not cache.delete(_key(kind, ticket)):
        return None
    return data
