from django.conf import settings
from rest_framework.permissions import BasePermission


def staff_mfa_ok(request) -> bool:
    """Адмін-доступ: is_staff + 2FA у профілі + саме ця сесія пройшла другий фактор.

    Самого факту «2FA увімкнена» недостатньо: сесія, відкрита лише паролем
    (до увімкнення 2FA), не отримує адмін-прав, поки не пройде вхід з кодом."""
    user = request.user
    if not (user and user.is_authenticated and user.is_active and user.is_staff):
        return False
    if settings.REQUIRE_2FA_FOR_STAFF:
        if not user.has_2fa:
            return False
        session = getattr(request, "session", None)
        if session is None or not session.get("mfa"):
            return False
    return True


class IsStaffWith2FA(BasePermission):
    message = "Потрібні права адміністратора та вхід з 2FA."

    def has_permission(self, request, view):
        return staff_mfa_ok(request)
