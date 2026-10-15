"""Ключі доступу (passkeys): вхід без пароля, другий фактор після пароля, керування в налаштуваннях.

Користувач сам обирає спосіб входу: пароль; пароль + код з додатка (2FA); ключ доступу
(Face ID / Touch ID / Windows Hello) — без пароля. Ключ доступу з перевіркою користувача
на пристрої є двофакторним, тож такий вхід відкриває й адмін-функції.
"""

import time

from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.audit import audit, client_ip
from apps.core.authentication import EnforceCsrfMixin
from apps.core.realtime import notify

from . import passkeys
from .models import Passkey, User
from .views import (
    PRE2FA_KEY,
    PRE2FA_MAX_ATTEMPTS,
    PRE2FA_TTL,
    _complete_login,
    _mfa_fail_key,
    _mfa_locked,
    _mfa_register_failure,
    _require_password,
)

MAX_PASSKEYS = 20


class CredentialSerializer(serializers.Serializer):
    credential = serializers.DictField()
    name = serializers.CharField(max_length=64, required=False, allow_blank=True)


def _login_failed():
    return Response({"detail": _("Не вдалося увійти з ключем доступу. Спробуйте ще раз або увійдіть паролем.")}, status=400)


# ═══════════════════════════ Вхід без пароля ═══════════════════════════


class PasskeyLoginBeginView(EnforceCsrfMixin, APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        if passkeys.ip_locked(client_ip(request)):
            return Response({"detail": _("Забагато невдалих спроб. Спробуйте через 15 хвилин.")}, status=429)
        return Response({"options": passkeys.authentication_options(request, purpose="login")})


class PasskeyLoginFinishView(EnforceCsrfMixin, APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        ip = client_ip(request)
        if passkeys.ip_locked(ip):
            return Response({"detail": _("Забагато невдалих спроб. Спробуйте через 15 хвилин.")}, status=429)
        ser = CredentialSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            pk = passkeys.authenticate(request, ser.validated_data["credential"], purpose="login")
        except passkeys.PasskeyError as exc:
            passkeys.register_ip_failure(ip)
            audit(request, "login.passkey_failed", reason=str(exc))
            return _login_failed()
        request.session.pop(PRE2FA_KEY, None)
        _complete_login(request, pk.user, "passkey")
        return Response({"status": "ok"})


# ═══════════════════════════ Другий фактор після пароля ═══════════════════════════


def _pending_user(request):
    pending = request.session.get(PRE2FA_KEY)
    if not pending or time.time() - pending["ts"] > PRE2FA_TTL:
        request.session.pop(PRE2FA_KEY, None)
        raise ValidationError({"detail": _("Сесія входу завершилась. Увійдіть знову.")})
    if pending["attempts"] >= PRE2FA_MAX_ATTEMPTS:
        request.session.pop(PRE2FA_KEY, None)
        raise PermissionDenied(_("Забагато спроб. Увійдіть знову."))
    user = User.objects.filter(pk=pending["uid"], is_active=True).first()
    if user is None or not user.has_passkeys:
        request.session.pop(PRE2FA_KEY, None)
        raise ValidationError({"detail": _("Сесія входу завершилась. Увійдіть знову.")})
    if _mfa_locked(user):
        request.session.pop(PRE2FA_KEY, None)
        raise PermissionDenied(_("Забагато невдалих кодів. Спробуйте пізніше."))
    return pending, user


class PasskeySecondFactorBeginView(EnforceCsrfMixin, APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "two_factor"

    def post(self, request):
        _pending, user = _pending_user(request)
        return Response({"options": passkeys.authentication_options(request, user=user, purpose="2fa")})


class PasskeySecondFactorFinishView(EnforceCsrfMixin, APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "two_factor"

    def post(self, request):
        pending, user = _pending_user(request)
        ser = CredentialSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            passkeys.authenticate(request, ser.validated_data["credential"], purpose="2fa", expected_user=user)
        except passkeys.PasskeyError as exc:
            pending["attempts"] += 1
            request.session[PRE2FA_KEY] = pending
            audit(request, "login.2fa_failed", user=user, method="passkey", reason=str(exc))
            _mfa_register_failure(request, user)
            return _login_failed()
        cache.delete(_mfa_fail_key(user))
        request.session.pop(PRE2FA_KEY, None)
        _complete_login(request, user, "passkey")
        return Response({"status": "ok"})


# ═══════════════════════════ Налаштування → Безпека ═══════════════════════════


class PasskeyListView(APIView):
    def get(self, request):
        return Response([passkeys.serialize(pk) for pk in request.user.passkeys.all()])


class PasskeyRegisterBeginView(APIView):
    """Новий ключ: одразу після входу — без пароля (так працює пропозиція «Увімкнути Face ID»),
    пізніше — лише з підтвердженням пароля."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "passkey_register"

    def post(self, request):
        if request.user.passkeys.count() >= MAX_PASSKEYS:
            raise ValidationError({"detail": _("Досягнуто максимуму ключів доступу (%(n)s).") % {"n": MAX_PASSKEYS}})
        fresh = time.time() - (request.session.get("auth_at") or 0) <= settings.PASSKEY_FRESH_LOGIN_SECONDS
        password = request.data.get("password")
        if not fresh or password:
            if not password:
                raise ValidationError({"password": [_("Підтвердьте пароль.")]})
            _require_password(request, str(password))
        options = passkeys.registration_options(request, request.user, platform=bool(request.data.get("platform")))
        return Response({"options": options})


class PasskeyRegisterFinishView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "passkey_register"

    def post(self, request):
        ser = CredentialSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            pk = passkeys.register(request, request.user, ser.validated_data["credential"], ser.validated_data.get("name", ""))
        except passkeys.PasskeyError as exc:
            audit(request, "passkey.add_failed", reason=str(exc))
            if str(exc) == "duplicate":
                raise ValidationError({"detail": _("Цей ключ доступу вже додано.")})
            raise ValidationError({"detail": _("Не вдалося додати ключ доступу. Спробуйте ще раз.")})
        audit(request, "passkey.added", target=str(pk.pk), name=pk.name)
        notify(
            request.user,
            "security",
            gettext_noop("Додано ключ доступу"),
            gettext_noop("«%(name)s». Якщо це не ви — видаліть його в налаштуваннях безпеки й змініть пароль."),
            "/settings/security",
            params={"name": pk.name},
        )
        return Response(passkeys.serialize(pk), status=201)


class PasskeyDetailView(APIView):
    def _get(self, request, pk) -> Passkey:
        obj = Passkey.objects.filter(pk=pk, user=request.user).first()
        if obj is None:
            raise NotFound()
        return obj

    def patch(self, request, pk):
        obj = self._get(request, pk)
        name = str(request.data.get("name") or "").strip()[:64]
        if not name:
            raise ValidationError({"name": [_("Вкажіть назву.")]})
        obj.name = name
        obj.save(update_fields=["name"])
        return Response(passkeys.serialize(obj))

    def delete(self, request, pk):
        obj = self._get(request, pk)
        _require_password(request, str(request.data.get("password") or ""))
        name = obj.name
        obj.delete()
        audit(request, "passkey.removed", target=str(pk), name=name)
        notify(
            request.user,
            "security",
            gettext_noop("Ключ доступу видалено"),
            gettext_noop("«%(name)s» більше не може входити в акаунт."),
            "/settings/security",
            params={"name": name},
        )
        return Response(status=204)
