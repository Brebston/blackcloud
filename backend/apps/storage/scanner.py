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


def _command(cmd: bytes, timeout: float = 5.0) -> str:
    """Проста команда clamd (zPING, zVERSION) — лише читання стану, нічого не змінює."""
    try:
        sock = socket.create_connection((settings.CLAMAV_HOST, settings.CLAMAV_PORT), timeout=timeout)
    except OSError as exc:
        raise ScanError(f"clamd недоступний: {exc}") from exc
    with sock:
        sock.sendall(b"z" + cmd + b"\0")
        reply = b""
        while not reply.endswith(b"\0"):
            data = sock.recv(4096)
            if not data:
                break
            reply += data
    return reply.rstrip(b"\0").decode(errors="replace").strip()


def status() -> dict:
    """Стан антивірусу для адмін-панелі: доступність, версія двигуна й баз сигнатур."""
    if not settings.CLAMAV_ENABLED:
        return {"enabled": False, "reachable": False, "version": "", "error": ""}
    try:
        pong = _command(b"PING")
        version = _command(b"VERSION")
    except (ScanError, OSError) as exc:
        return {"enabled": True, "reachable": False, "version": "", "error": str(exc)[:200]}
    # Формат: "ClamAV 1.0.7/27420/Tue Oct  8 08:12:34 2026"
    return {"enabled": True, "reachable": pong == "PONG", "version": version[:200], "error": ""}
