"""Мінімальний клієнт clamd (протокол INSTREAM) без сторонніх бібліотек."""

import socket
import struct
from collections.abc import Iterable

from django.conf import settings


class ScanError(Exception):
    pass


def scan_stream(chunks: Iterable[bytes], timeout: float = 600.0) -> tuple[bool, str]:
    """Повертає (clean, details). Кидає ScanError, якщо clamd недоступний."""
    try:
        sock = socket.create_connection((settings.CLAMAV_HOST, settings.CLAMAV_PORT), timeout=timeout)
    except OSError as exc:
        raise ScanError(f"clamd недоступний: {exc}") from exc
    with sock:
        sock.sendall(b"zINSTREAM\0")
        for chunk in chunks:
            for i in range(0, len(chunk), 1 << 20):
                part = chunk[i : i + (1 << 20)]
                sock.sendall(struct.pack(">I", len(part)) + part)
        sock.sendall(struct.pack(">I", 0))
        reply = b""
        while not reply.endswith(b"\0"):
            data = sock.recv(4096)
            if not data:
                break
            reply += data
    text = reply.rstrip(b"\0").decode(errors="replace").strip()
    if text.endswith("OK"):
        return True, ""
    if text.endswith("FOUND"):
        signature = text.removeprefix("stream:").removesuffix("FOUND").strip()
        return False, signature[:200]
    raise ScanError(text[:200] or "порожня відповідь clamd")
