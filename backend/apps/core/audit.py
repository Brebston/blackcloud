import logging

from .models import AuditLog

logger = logging.getLogger("blackcloud.audit")


def client_ip(request) -> str | None:
    """IP клієнта.

    X-Forwarded-For тут НЕ читається: його розбирає uvicorn (--proxy-headers) і лише
    для запитів від Traefik (--forwarded-allow-ips = фіксована адреса Traefik).
    Тому REMOTE_ADDR — це або справжня адреса клієнта, або адреса сусіднього
    контейнера, але ніколи не значення, яке клієнт підставив у заголовок."""
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR") or None


def audit(request, action: str, *, user=None, target: str = "", **metadata) -> None:
    if user is None and request is not None and getattr(request, "user", None) is not None:
        user = request.user if request.user.is_authenticated else None
    ua = request.META.get("HTTP_USER_AGENT", "")[:300] if request is not None else ""
    ip = client_ip(request)
    try:
        AuditLog.objects.create(
            user=user, action=action, ip_address=ip, user_agent=ua, target=str(target)[:200], metadata=metadata
        )
    except Exception:  # аудит не повинен ламати основну дію
        logger.exception("audit write failed")
    logger.info("audit action=%s user=%s ip=%s target=%s", action, getattr(user, "pk", None), ip, target)
