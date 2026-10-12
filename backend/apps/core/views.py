import hmac
import logging
import shutil
import socket
import ssl
import time
from datetime import datetime
from datetime import timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import Http404, JsonResponse
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from .models import Notification


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception:
        return JsonResponse({"status": "db_unavailable"}, status=503)


# ─── Глибока перевірка стану для моніторингу (Uptime Kuma) ─────────────
# GET /api/health/deep/ з заголовком "Authorization: Bearer <health_token>".
# 200 — усе гаразд, 503 — хоча б одна перевірка не пройшла. Відповідь містить лише
# короткі коди причин, без трейсбеків, адрес і секретів.

logger = logging.getLogger("blackcloud")
CELERY_HEARTBEAT_KEY = "health:celery_heartbeat"
TLS_TIMEOUT = 5.0


class HealthDeepThrottle(AnonRateThrottle):
    scope = "health_deep"
    rate = "30/min"


def _check_db() -> dict:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return {"ok": True}


def _check_cache() -> dict:
    key = "health:probe"
    value = str(time.time())
    cache.set(key, value, 30)
    return {"ok": cache.get(key) == value}


def _check_clamav() -> dict:
    from apps.storage import scanner

    st = scanner.status()
    if not st.get("enabled"):
        return {"ok": True, "enabled": False}
    # Текст помилки (адреса/порт) не віддаємо — лише ознаку доступності
    return {"ok": bool(st.get("reachable")), "enabled": True}


def _check_disk() -> dict:
    path = Path(settings.LOCAL_OBJECT_STORE_DIR)
    # Каталог може ще не існувати — міряємо найближчий наявний батьківський
    while not path.exists() and path != path.parent:
        path = path.parent
    usage = shutil.disk_usage(path)
    percent = round(usage.used * 100 / usage.total, 1) if usage.total else 0.0
    return {
        "ok": percent < settings.HEALTH_DISK_ALERT_PERCENT,
        "percent": percent,
        "free_gb": round(usage.free / 1024**3, 1),
        "threshold": settings.HEALTH_DISK_ALERT_PERCENT,
    }


def _peer_cert_der(host: str, port: int, server_name: str) -> bytes:
    """Сертифікат, який віддає сервер для server_name (SNI). Перевірку ланцюжка вимкнено
    навмисно: потрібна лише дата закінчення, а довіру перевіряє зовнішній HTTPS-монітор."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with (
        socket.create_connection((host, port), timeout=TLS_TIMEOUT) as sock,
        ctx.wrap_socket(sock, server_hostname=server_name) as tls,
    ):
        der = tls.getpeercert(binary_form=True)
    if not der:
        raise ValueError("no certificate")
    return der


def _cert_days_left(der: bytes) -> tuple[float, datetime]:
    from cryptography import x509

    cert = x509.load_der_x509_certificate(der)
    not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=dt_timezone.utc)
    days = (not_after - datetime.now(dt_timezone.utc)).total_seconds() / 86400
    return days, not_after


def _check_cert(host: str, port: int, server_name: str) -> dict:
    days, not_after = _cert_days_left(_peer_cert_der(host, port, server_name))
    return {
        "ok": days >= settings.HEALTH_CERT_MIN_DAYS,
        "days_left": int(days),
        "not_after": not_after.date().isoformat(),
    }


def _check_cert_web() -> dict | None:
    if settings.IS_LOCAL_DOMAIN:
        return None
    return _check_cert(settings.HEALTH_TLS_HOST, 443, settings.DOMAIN)


def _check_cert_mail() -> dict | None:
    if not settings.MAIL_HOSTNAME or not settings.HEALTH_MAIL_TLS_HOST:
        return None
    return _check_cert(settings.HEALTH_MAIL_TLS_HOST, settings.HEALTH_MAIL_TLS_PORT, settings.MAIL_HOSTNAME)


def _check_celery() -> dict:
    """Мітку щохвилини оновлює задача core.celery_heartbeat (beat → черга → worker)."""
    last = cache.get(CELERY_HEARTBEAT_KEY)
    if last is None:
        return {"ok": False, "reason": "no_heartbeat"}
    age = max(0, int(time.time() - float(last)))
    return {"ok": age <= settings.HEALTH_CELERY_MAX_AGE, "age_seconds": age}


HEALTH_CHECKS = {
    "db": _check_db,
    "cache": _check_cache,
    "clamav": _check_clamav,
    "disk": _check_disk,
    "cert_web": _check_cert_web,
    "cert_mail": _check_cert_mail,
    "celery": _check_celery,
}


def run_health_checks() -> tuple[bool, dict]:
    results = {}
    for name, check in HEALTH_CHECKS.items():
        try:
            result = check()
            if result is None:
                result = {"ok": True, "skipped": True}
        except (OSError, ssl.SSLError) as exc:
            result = {"ok": False, "reason": type(exc).__name__}
        except Exception as exc:  # noqa: BLE001 — одна зламана перевірка не валить решту
            logger.warning("health check %s failed: %s", name, type(exc).__name__)
            result = {"ok": False, "reason": "error"}
        results[name] = result
    return all(r["ok"] for r in results.values()), results


class HealthDeepView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [HealthDeepThrottle]

    def initial(self, request, *args, **kwargs):
        # Без налаштованого токена ендпоінта «не існує»
        if not settings.HEALTH_TOKEN:
            raise Http404
        super().initial(request, *args, **kwargs)

    def get(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        scheme, _sep, token = header.partition(" ")
        expected = settings.HEALTH_TOKEN
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip().encode(), expected.encode()):
            raise PermissionDenied()
        ok, checks = run_health_checks()
        return Response({"status": "ok" if ok else "fail", "checks": checks}, status=200 if ok else 503)


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "kind", "title", "body", "link", "created_at", "read_at"]
        read_only_fields = fields


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer

    def get_queryset(self):
        qs = Notification.objects.filter(user=self.request.user)
        if self.request.query_params.get("unread") == "1":
            qs = qs.filter(read_at__isnull=True)
        return qs

    @action(detail=False, methods=["post"])
    def mark_all_read(self, request):
        Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
        return Response({"ok": True})
