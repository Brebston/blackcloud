import logging

from .models import AuditLog

logger = logging.getLogger("blackcloud.audit")


def client_ip(request) -> str | None:
    """IP клієнта. За Traefik довіряємо лише останньому значенню X-Forwarded-For,
    яке додає сам Traefik (клієнт не може його підробити)."""
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[-1].strip() or None
    return request.META.get("REMOTE_ADDR")


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
