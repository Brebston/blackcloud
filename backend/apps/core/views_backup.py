from django.utils.translation import gettext as _
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.audit import audit
from apps.core.permissions import IsStaffWith2FA

from . import backups


class Conflict(APIException):
    status_code = 409


class Unavailable(APIException):
    status_code = 503


class BackupStatusView(APIView):
    """Стан резервних копій: остання копія й перевірка, історія, наступний запуск."""

    permission_classes = [IsStaffWith2FA]

    def get(self, request):
        status = backups.read_status()
        return Response(
            {
                "configured": status is not None,
                "status": status,
                "stale": backups.is_stale(status) if status is not None else None,
                "running": backups.is_running(status),
                "request_pending": backups.request_pending(),
                "can_request": backups.can_request(),
            }
        )


class BackupRunView(APIView):
    """«Зробити копію зараз»: лише ставить запит; копію запускає systemd на хості."""

    permission_classes = [IsStaffWith2FA]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "backup_run"

    def post(self, request):
        if not backups.can_request():
            raise Unavailable(_("Резервні копії не налаштовані на сервері (див. docs/BACKUP.md)."))
        status = backups.read_status()
        if backups.is_running(status):
            raise Conflict(_("Резервна копія вже виконується."))
        if backups.request_pending():
            raise Conflict(_("Запит уже надіслано — копія почнеться за кілька секунд."))
        backups.create_request(request.user.username)
        audit(request, "backup.requested")
        return Response({"status": "requested"}, status=202)
