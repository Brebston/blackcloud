"""Стан резервних копій для адмінки.

Копії робить хост (systemd + restic, scripts/backup.sh), а не застосунок: веб-процес
не має ні пароля restic, ні доступу до Storage Box. Обмін — лише через два каталоги:

* <BACKUP_STATE_DIR>/status/status.json — пише скрипт (root), сюди змонтовано лише для читання;
* <BACKUP_STATE_DIR>/request/run — файл-запит «зробити копію зараз». Його створює адмінка,
  а systemd (blackcloud-backup-request.path) бачить файл і запускає звичайну копію.
  Веб-процес нічого не виконує на хості й не може видалити чи змінити копії.
"""

import json
import os
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

MAX_STATUS_BYTES = 512 * 1024


def _status_path() -> str:
    return os.path.join(settings.BACKUP_STATE_DIR, "status", "status.json")


def _request_dir() -> str:
    return os.path.join(settings.BACKUP_STATE_DIR, "request")


def request_path() -> str:
    return os.path.join(_request_dir(), "run")


def _parse_dt(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else timezone.make_aware(dt, timezone.utc)


def read_status() -> dict | None:
    """Вміст status.json або None, якщо копії ще не налаштовані на цьому сервері."""
    try:
        with open(_status_path(), "rb") as fh:
            raw = fh.read(MAX_STATUS_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_STATUS_BYTES:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def last_success(status: dict | None):
    return _parse_dt((status or {}).get("last_success_at"))


def is_stale(status: dict | None) -> bool:
    """Остання вдала копія давніша за BACKUP_MAX_AGE_HOURS (або її не було)."""
    when = last_success(status)
    return when is None or timezone.now() - when > timedelta(hours=settings.BACKUP_MAX_AGE_HOURS)


def is_running(status: dict | None) -> bool:
    last = (status or {}).get("last_backup") or {}
    if last.get("status") != "running":
        return False
    started = _parse_dt(last.get("started_at"))
    # «Завислий» стан (скрипт убили) не блокує кнопку назавжди
    return started is not None and timezone.now() - started < timedelta(hours=6)


def request_pending() -> bool:
    return os.path.exists(request_path())


def can_request() -> bool:
    return os.path.isdir(_request_dir()) and os.access(_request_dir(), os.W_OK)


def create_request(username: str) -> None:
    payload = json.dumps({"user": username[:64], "at": timezone.now().isoformat(timespec="seconds")})
    tmp = os.path.join(_request_dir(), ".run.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(payload)
    os.replace(tmp, request_path())  # атомарно: systemd бачить уже повний файл
