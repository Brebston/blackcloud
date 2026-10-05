"""Шифрування файлів на рівні застосунку (envelope encryption).

* Для кожного файлу генерується випадковий 256-бітний ключ даних (DEK).
* DEK шифрується майстер-ключем (KEK) з Docker secret і зберігається в БД.
* Кожен чанк шифрується AES-256-GCM з унікальним nonce; у AAD входять
  id файлу та номер чанка — чанки не можна переставити чи підмінити між файлами.
* На диску/в S3 лежить лише шифротекст; викрадення сховища не розкриває дані.
* Ротація: у file_master_keys можна додати новий ключ із більшою версією.
"""

import base64
import os
import uuid
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings

NONCE_LEN = 12


@lru_cache(maxsize=1)
def _master_keys() -> dict[int, bytes]:
    raw = settings.FILE_MASTER_KEYS or ""
    keys: dict[int, bytes] = {}
    for line in raw.replace(",", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        version, _, b64 = line.partition(":")
        key = base64.b64decode(b64)
        if len(key) != 32:
            raise RuntimeError(f"Майстер-ключ версії {version} має бути 32 байти")
        keys[int(version)] = key
    if not keys:
        raise RuntimeError("FILE_MASTER_KEYS не задано")
    return keys


def current_key_version() -> int:
    return max(_master_keys())


def new_data_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def wrap_key(data_key: bytes, file_id: uuid.UUID) -> tuple[bytes, int]:
    version = current_key_version()
    nonce = os.urandom(NONCE_LEN)
    aad = b"bc-dek:" + file_id.bytes
    wrapped = nonce + AESGCM(_master_keys()[version]).encrypt(nonce, data_key, aad)
    return wrapped, version


def unwrap_key(wrapped: bytes, version: int, file_id: uuid.UUID) -> bytes:
    wrapped = bytes(wrapped)
    nonce, ct = wrapped[:NONCE_LEN], wrapped[NONCE_LEN:]
    return AESGCM(_master_keys()[version]).decrypt(nonce, ct, b"bc-dek:" + file_id.bytes)


# Окремий "індекс" для мініатюри, щоб її шифротекст не можна було підставити замість чанка
THUMB_INDEX = 2**62


def _chunk_aad(file_id: uuid.UUID, index: int) -> bytes:
    return b"bc-chunk:" + file_id.bytes + index.to_bytes(8, "big")


def encrypt_chunk(data_key: bytes, file_id: uuid.UUID, index: int, plaintext: bytes) -> bytes:
    nonce = os.urandom(NONCE_LEN)
    return nonce + AESGCM(data_key).encrypt(nonce, plaintext, _chunk_aad(file_id, index))


def decrypt_chunk(data_key: bytes, file_id: uuid.UUID, index: int, blob: bytes) -> bytes:
    nonce, ct = blob[:NONCE_LEN], blob[NONCE_LEN:]
    return AESGCM(data_key).decrypt(nonce, ct, _chunk_aad(file_id, index))
