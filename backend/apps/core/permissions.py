from django.conf import settings
from rest_framework.permissions import BasePermission


class IsStaffWith2FA(BasePermission):
    message = "Потрібні права адміністратора та увімкнена 2FA."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.is_staff):
            return False
        if settings.REQUIRE_2FA_FOR_STAFF and not user.has_2fa:
            return False
        return True
