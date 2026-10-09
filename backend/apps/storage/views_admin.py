"""Адмін-керування антивірусом і карантином.

Карантин — це файли зі статусом «заражений», «помилка перевірки» або «не перевірено».
Вони лишаються зашифрованими у сховищі й недоступні для завантаження, перегляду
та спільного доступу. Тут адміністратор може:
  * переглянути їх (без вмісту — лише метадані й сигнатуру);
  * поставити на повторну перевірку ClamAV (рішення знову ухвалює антивірус);
  * остаточно видалити (з підтвердженням пароля).
Ручного «звільнення» з карантину навмисно немає: це обходило б антивірус.
"""

from django.conf import settings
from django.db.models import Count
from django.utils.translation import gettext as _, gettext_noop
from rest_framework import status as http_status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.views import _require_password
from apps.core.audit import audit
from apps.core.permissions import IsStaffWith2FA
from apps.core.realtime import notify, push_to_user

from . import scanner, services
from .models import File

QUARANTINE_STATUSES = (File.Status.INFECTED, File.Status.FAILED, File.Status.UNSCANNED)
MAX_BULK_RESCAN = 500


def _quarantined(pk) -> File:
    f = File.objects.select_related("owner").filter(pk=pk, status__in=QUARANTINE_STATUSES).first()
    if f is None:
        raise NotFound(_("Файл не знайдено в карантині."))
    return f


def _check_owner(request, f: File):
    """Файли адміністраторів може видаляти лише суперкористувач (як і керувати їхніми акаунтами)."""
    owner = f.owner
    if request.user.is_superuser or owner == request.user:
        return
    if owner.is_staff or owner.is_superuser:
        raise PermissionDenied(_("Файли адміністраторів може видаляти лише суперкористувач."))


def _serialize(f: File) -> dict:
    return {
        "id": str(f.pk),
        "name": f.name,
        "owner": f.owner.username,
        "size": f.size,
        "mime_type": f.mime_type,
        "status": f.status,
        "status_display": f.get_status_display(),
        "scan_detail": f.scan_detail,
        "sha256": f.sha256,
        "in_trash": f.deleted_at is not None,
        "created_at": f.created_at,
    }


class QuarantineListView(APIView):
    permission_classes = [IsStaffWith2FA]

    def get(self, request):
        wanted = request.query_params.get("status") or ""
        statuses = [wanted] if wanted in QUARANTINE_STATUSES else list(QUARANTINE_STATUSES)
        qs = File.objects.filter(status__in=statuses).select_related("owner").order_by("-created_at")[:500]
        return Response([_serialize(f) for f in qs])


class QuarantineRescanView(APIView):
    """Повторна перевірка: файл знову проходить ClamAV. Якщо бази оновились і файл
    чистий — він стане доступним; якщо ні — лишиться в карантині."""

    permission_classes = [IsStaffWith2FA]

    def post(self, request, pk):
        from .tasks import scan_file

        f = _quarantined(pk)
        if not settings.CLAMAV_ENABLED:
            raise ValidationError({"detail": _("Антивірус вимкнено в конфігурації — повторна перевірка неможлива.")})
        updated = File.objects.filter(pk=f.pk, status=f.status).update(status=File.Status.SCANNING, scan_detail="")
        if updated:
            scan_file.delay(str(f.pk))
            push_to_user(f.owner_id, "file.updated", {"id": str(f.pk), "status": File.Status.SCANNING})
        audit(request, "admin.quarantine_rescan", target=str(f.pk), previous=f.status)
        return Response({"status": File.Status.SCANNING})


class QuarantinePurgeView(APIView):
    """Остаточне видалення файлу з карантину. Потребує пароля адміністратора."""

    permission_classes = [IsStaffWith2FA]

    def post(self, request, pk):
        f = _quarantined(pk)
        _check_owner(request, f)
        _require_password(request, request.data.get("password") or "")
        name, owner, signature = f.name, f.owner, f.scan_detail
        services.purge_file(f)
        audit(request, "admin.quarantine_purge", target=str(pk), owner=owner.username, signature=signature)
        if owner != request.user:
            notify(
                owner,
                "security",
                gettext_noop("Файл з карантину видалено"),
                gettext_noop("Адміністратор остаточно видалив «%(name)s»."),
                "/files",
                params={"name": name},
            )
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class AntivirusView(APIView):
    """Стан антивірусу. Вимкнути антивірус звідси неможливо — лише через конфігурацію сервера."""

    permission_classes = [IsStaffWith2FA]

    def get(self, request):
        counts = dict(File.objects.order_by().values("status").annotate(n=Count("pk")).values_list("status", "n"))
        return Response(
            {
                **scanner.status(),
                "unscanned_policy": settings.UNSCANNED_POLICY,
                "max_scan_bytes": settings.CLAMAV_MAX_STREAM,
                "counts": {s: counts.get(s, 0) for s in File.Status.values},
            }
        )


class AntivirusRescanFailedView(APIView):
    """Масова повторна перевірка файлів зі статусом «помилка» (наприклад, після збою ClamAV)."""

    permission_classes = [IsStaffWith2FA]

    def post(self, request):
        from .tasks import scan_file

        _require_password(request, request.data.get("password") or "")
        if not settings.CLAMAV_ENABLED:
            raise ValidationError({"detail": _("Антивірус вимкнено в конфігурації.")})
        ids = list(File.objects.filter(status=File.Status.FAILED).values_list("pk", flat=True)[:MAX_BULK_RESCAN])
        File.objects.filter(pk__in=ids, status=File.Status.FAILED).update(status=File.Status.SCANNING, scan_detail="")
        for pk in ids:
            scan_file.delay(str(pk))
        audit(request, "admin.antivirus_rescan_failed", count=len(ids))
        return Response({"queued": len(ids)})
