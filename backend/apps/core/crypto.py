"""Шифрування чутливих полів у БД (TOTP-секрети тощо) за допомогою Fernet."""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    if not settings.FIELD_ENCRYPTION_KEY:
        raise RuntimeError("FIELD_ENCRYPTION_KEY не задано")
    return Fernet(settings.FIELD_ENCRYPTION_KEY.encode())


def encrypt_str(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_str(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Не вдалося розшифрувати поле") from exc
