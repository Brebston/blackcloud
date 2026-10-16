"""Адмінка → «Стан сервера»: усе важливе на одному екрані.

Диск і пам'ять хоста, навантаження, черги антивірусу й мініатюр, черги Celery, служби,
резервні копії й терміни дії сертифікатів. Дані лише читаються; жодних секретів, адрес
чи текстів помилок у відповіді немає — тільки числа й короткі коди.

Сповіщення (зокрема «диск заповнений на 85%») надсилає Uptime Kuma за /api/health/deep/
(див. docs/MONITORING.md), а не ця сторінка.
"""

import logging
import os
import ssl

from django.conf import settings
from django.core.cache import cache
from django.db.models import Min, Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from . import backups
from . import views as health
from .permissions import IsStaffWith2FA

logger = logging.getLogger("blackcloud")

CERT_CACHE_KEY = "admin-status:certs"
CERT_CACHE_TTL = 600  # TLS-рукостискання не робимо на кожне оновлення сторінки
MEMORY_ALERT_PERCENT = 90
SCAN_STUCK_MINUTES = 30
CELERY_QUEUES = ("default", "scan", "push")


def _safe(fn, *args):
    try:
        return fn(*args)
    except (OSError, ssl.SSLError) as exc:
        return {"ok": False, "reason": type(exc).__name__}
    except Exception as exc:  # noqa: BLE001 — один зламаний блок не валить сторінку
        logger.warning("admin status: %s failed: %s", getattr(fn, "__name__", fn), type(exc).__name__)
        return {"ok": False, "reason": "error"}


def _disk() -> dict:
    usage, percent = health.disk_usage()
    return {
        "ok": percent < settings.HEALTH_DISK_ALERT_PERCENT,
        "percent": percent,
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "threshold": settings.HEALTH_DISK_ALERT_PERCENT,
    }


def _read_meminfo(path: str = "/proc/meminfo") -> dict:
    values = {}
    with open(path) as fh:
        for line in fh:
            key, _sep, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                values[key.strip()] = int(parts[0]) * 1024  # kB → байти
    return values


def _memory() -> dict:
    """Пам'ять хоста (у контейнері /proc/meminfo показує весь сервер)."""
    m = _read_meminfo()
    total = m.get("MemTotal", 0)
    available = m.get("MemAvailable", m.get("MemFree", 0))
    used = max(0, total - available)
    percent = round(used * 100 / total, 1) if total else 0.0
    swap_total = m.get("SwapTotal", 0)
    swap_used = max(0, swap_total - m.get("SwapFree", 0))
    return {
        "ok": percent < MEMORY_ALERT_PERCENT,
        "total": total,
        "used": used,
        "available": available,
        "percent": percent,
        "swap_total": swap_total,
        "swap_used": swap_used,
    }


def _load() -> dict:
    l1, l5, l15 = os.getloadavg()
    cpus = os.cpu_count() or 1
    uptime = None
    try:
        with open("/proc/uptime") as fh:
            uptime = int(float(fh.read().split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    return {"ok": l5 < cpus * 2, "load1": round(l1, 2), "load5": round(l5, 2), "load15": round(l15, 2), "cpus": cpus, "uptime_s": uptime}


def _celery_queues() -> dict:
    """Скільки задач чекає в кожній черзі Celery (довжини списків у Redis-брокері)."""
    if settings.CELERY_TASK_ALWAYS_EAGER:
        return {"ok": True, "skipped": True}
    import redis

    client = redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_timeout=2, socket_connect_timeout=2)
    try:
        pipe = client.pipeline()
        for name in CELERY_QUEUES:
            pipe.llen(name)
        lengths = dict(zip(CELERY_QUEUES, pipe.execute(), strict=True))
    finally:
        client.close()
    return {"ok": True, "lengths": lengths}


def _antivirus() -> dict:
    from apps.storage.models import File, FileVersion

    scanning = File.objects.filter(status=File.Status.SCANNING, deleted_at__isnull=True)
    oldest = scanning.aggregate(t=Min("updated_at"))["t"]
    oldest_min = int((timezone.now() - oldest).total_seconds() // 60) if oldest else None
    engine = health._check_clamav()
    return {
        "ok": bool(engine.get("ok")) and (oldest_min is None or oldest_min < SCAN_STUCK_MINUTES),
        "enabled": engine.get("enabled", True),
        "reachable": bool(engine.get("ok")),
        "pending": scanning.count(),
        "pending_versions": FileVersion.objects.filter(status=File.Status.SCANNING).count(),
        "oldest_minutes": oldest_min,
        "infected": File.objects.filter(status=File.Status.INFECTED).count(),
        "unscanned": File.objects.filter(status=File.Status.UNSCANNED, deleted_at__isnull=True).count(),
    }


def _thumbnails() -> dict:
    from apps.storage import thumbnails as th
    from apps.storage.models import File

    if not th.enabled():
        return {"ok": True, "enabled": False}
    # Приблизно: файли підтримуваних типів без мініатюри (backfill догенерує їх кожні 30 хв)
    kinds = (
        Q(mime_type__in=th.IMAGE_MIME, size__lte=th.MAX_IMAGE_BYTES)
        | Q(mime_type="application/pdf", size__lte=th.MAX_PDF_BYTES)
        | Q(mime_type__in=th.VIDEO_MIME, size__gt=0, size__lte=th.MAX_VIDEO_BYTES)
    )
    pending = File.objects.filter(status=File.Status.CLEAN, deleted_at__isnull=True, has_thumbnail=False).filter(kinds).count()
    return {"ok": True, "enabled": True, "pending": pending}


def _backup() -> dict:
    status = backups.read_status()
    if status is None:
        return {"ok": True, "configured": False}
    last = status.get("last_backup") or {}
    check = status.get("last_check") or {}
    stale = backups.is_stale(status)
    return {
        "ok": not stale and last.get("status") != "failed",
        "configured": True,
        "last_success_at": status.get("last_success_at"),
        "last_status": last.get("status"),
        "running": backups.is_running(status),
        "stale": stale,
        "next_backup": (status.get("schedule") or {}).get("next_backup"),
        "check_status": check.get("status"),
        "repo_size_bytes": status.get("repo_size_bytes"),
    }


def _certs(refresh: bool = False) -> dict:
    data = None if refresh else cache.get(CERT_CACHE_KEY)
    if data is None:
        data = {}
        for name, check in (("web", health._check_cert_web), ("mail", health._check_cert_mail)):
            result = _safe(check)
            data[name] = {"ok": True, "skipped": True} if result is None else result
        data["checked_at"] = timezone.now().isoformat()
        cache.set(CERT_CACHE_KEY, data, CERT_CACHE_TTL)
    return data


def _services() -> dict:
    out = {}
    for name in ("db", "cache", "celery"):
        out[name] = _safe(health.HEALTH_CHECKS[name])
    return out


def build_status(refresh: bool = False) -> dict:
    data = {
        "disk": _safe(_disk),
        "memory": _safe(_memory),
        "load": _safe(_load),
        "antivirus": _safe(_antivirus),
        "thumbnails": _safe(_thumbnails),
        "celery_queues": _safe(_celery_queues),
        "backup": _safe(_backup),
        "certs": _certs(refresh),
        "services": _services(),
    }
    problems = [k for k in ("disk", "memory", "load", "antivirus", "thumbnails", "celery_queues", "backup") if not data[k].get("ok", False)]
    problems += [f"cert_{k}" for k in ("web", "mail") if not data["certs"][k].get("ok", False)]
    problems += [k for k, v in data["services"].items() if not v.get("ok", False)]
    data["problems"] = problems
    data["ok"] = not problems
    data["thresholds"] = {
        "disk_percent": settings.HEALTH_DISK_ALERT_PERCENT,
        "memory_percent": MEMORY_ALERT_PERCENT,
        "cert_days": settings.HEALTH_CERT_MIN_DAYS,
        "scan_stuck_minutes": SCAN_STUCK_MINUTES,
    }
    data["generated_at"] = timezone.now().isoformat()
    return data


class ServerStatusView(APIView):
    permission_classes = [IsStaffWith2FA]

    def get(self, request):
        return Response(build_status(refresh=request.query_params.get("refresh") == "1"))
