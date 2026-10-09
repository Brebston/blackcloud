import time

from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, gettext_noop
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.audit import audit, client_ip
from apps.core.models import AuditLog
from apps.core.permissions import IsStaffWith2FA
from apps.core.realtime import alert_staff, close_session_sockets, close_user_sockets, notify

from . import lockout, twofactor
from .models import Invite, Preferences, User, UserSession
from .serializers import (
    AdminCreateUserSerializer,
    AdminUserSerializer,
    ChangePasswordSerializer,
    DisableTwoFactorSerializer,
    InviteSerializer,
    LoginSerializer,
    PasswordConfirmSerializer,
    PreferencesSerializer,
    ProfileSerializer,
    PublicUserSerializer,
    RegisterSerializer,
    SessionSerializer,
    TwoFactorSerializer,
    UserSerializer,
    session_public_id,
)

PRE2FA_KEY = "pre2fa"
PRE2FA_TTL = 300
PRE2FA_MAX_ATTEMPTS = 5
GENERIC_LOGIN_ERROR = gettext_lazy("Невірний логін або пароль.")


# ─── Лічильник невдалих 2FA на акаунт ───
# Не залежить від IP і НЕ скидається успішним паролем: інакше зловмисник із
# відомим паролем міг би перебирати коди нескінченними циклами «пароль → 5 кодів».
def _mfa_fail_key(user) -> str:
    return f"mfa-fail:{user.pk}"


def _mfa_locked(user) -> bool:
    return (cache.get(_mfa_fail_key(user)) or 0) >= settings.TWO_FACTOR_MAX_FAILURES


def _mfa_register_failure(request, user) -> None:
    key = _mfa_fail_key(user)
    if cache.add(key, 1, settings.TWO_FACTOR_FAILURE_WINDOW):
        count = 1
    else:
        try:
            count = cache.incr(key)
        except ValueError:
            cache.set(key, 1, settings.TWO_FACTOR_FAILURE_WINDOW)
            count = 1
    if count == settings.TWO_FACTOR_MAX_FAILURES:
        audit(request, "login.2fa_locked", user=user)
        notify(
            user,
            "security",
            gettext_noop("Хтось підбирає код 2FA до вашого акаунта"),
            gettext_noop("Пароль введено правильно, але код — ні. Вхід тимчасово заблоковано. Змініть пароль."),
            "/settings/security",
        )
        alert_staff(
            gettext_noop("Підбір 2FA"),
            gettext_noop("Акаунт %(username)s: %(count)s невдалих кодів за годину."),
            params={"username": user.username, "count": count},
        )


def _resolve_username(login_value: str) -> str:
    login_value = login_value.strip().lower()
    if "@" in login_value:
        user = User.objects.filter(email=login_value).only("username").first()
        return user.username if user else login_value
    return login_value


def _complete_login(request, user, method: str):
    request.session.cycle_key()  # захист від session fixation
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session["auth_at"] = int(time.time())
    request.session["mfa"] = method != "password"
    ip = client_ip(request)
    known_ip = AuditLog.objects.filter(user=user, action="login.success", ip_address=ip).exists()
    audit(request, "login.success", user=user, method=method)
    if not known_ip and ip:
        notify(
            user,
            "security",
            gettext_noop("Новий вхід в акаунт"),
            gettext_noop("Вхід з IP %(ip)s. Якщо це не ви — змініть пароль."),
            "/settings/security",
            params={"ip": ip},
        )


def _require_password(request, password: str):
    """Підтвердження пароля для чутливих дій (з урахуванням блокування)."""
    user = request.user
    ip = client_ip(request)
    if lockout.is_locked(user.username, ip):
        raise PermissionDenied(_("Забагато невдалих спроб. Спробуйте пізніше."))
    if not user.check_password(password):
        lockout.register_failure(user.username, ip)
        audit(request, "reauth.failed")
        raise ValidationError({"password": [_("Невірний пароль.")]})
    request.session["auth_at"] = int(time.time())


# ═══════════════════════════ Автентифікація ═══════════════════════════


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"csrfToken": get_token(request)})


class MeView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        if not request.user.is_authenticated:
            pending = request.session.get(PRE2FA_KEY)
            return Response({"authenticated": False, "two_factor_pending": bool(pending)})
        user = request.user
        data = UserSerializer(user).data
        data["preferences"] = PreferencesSerializer(Preferences.objects.get_or_create(user=user)[0]).data
        data["must_enroll_2fa"] = bool(user.is_staff and settings.REQUIRE_2FA_FOR_STAFF and not user.has_2fa)
        data["features"] = {"office": bool(settings.OFFICE_ENABLED and settings.OFFICE_JWT_SECRET)}
        return Response({"authenticated": True, "user": data})


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        ser = LoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        login_value = ser.validated_data["login"].strip().lower()
        ip = client_ip(request)

        if lockout.is_locked(login_value, ip):
            audit(request, "login.locked", target=login_value)
            return Response(
                {"detail": _("Забагато невдалих спроб. Спробуйте через 15 хвилин.")},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        username = _resolve_username(login_value)
        user = authenticate(request, username=username, password=ser.validated_data["password"])
        if user is None or not user.is_active:
            lockout.register_failure(login_value, ip)
            audit(request, "login.failed", target=login_value)
            return Response({"detail": GENERIC_LOGIN_ERROR}, status=status.HTTP_400_BAD_REQUEST)

        lockout.reset(login_value)

        if user.has_2fa:
            if _mfa_locked(user):
                audit(request, "login.2fa_locked", user=user)
                return Response(
                    {"detail": _("Забагато невдалих кодів. Спробуйте пізніше.")},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            request.session.cycle_key()
            request.session[PRE2FA_KEY] = {"uid": str(user.pk), "ts": int(time.time()), "attempts": 0}
            audit(request, "login.password_ok", user=user)
            return Response({"status": "2fa_required"})

        _complete_login(request, user, "password")
        return Response({"status": "ok"})


class LoginTwoFactorView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "two_factor"

    def post(self, request):
        pending = request.session.get(PRE2FA_KEY)
        if not pending or time.time() - pending["ts"] > PRE2FA_TTL:
            request.session.pop(PRE2FA_KEY, None)
            return Response({"detail": _("Сесія входу завершилась. Увійдіть знову.")}, status=400)
        if pending["attempts"] >= PRE2FA_MAX_ATTEMPTS:
            request.session.pop(PRE2FA_KEY, None)
            return Response({"detail": _("Забагато спроб. Увійдіть знову.")}, status=429)

        ser = TwoFactorSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = User.objects.filter(pk=pending["uid"], is_active=True).select_related("totp_device").first()
        if user is None:
            request.session.pop(PRE2FA_KEY, None)
            return Response({"detail": GENERIC_LOGIN_ERROR}, status=400)

        if _mfa_locked(user):
            request.session.pop(PRE2FA_KEY, None)
            return Response({"detail": _("Забагато невдалих кодів. Спробуйте пізніше.")}, status=429)

        method = twofactor.verify_second_factor(user, ser.validated_data["code"])
        if method is None:
            pending["attempts"] += 1
            request.session[PRE2FA_KEY] = pending
            audit(request, "login.2fa_failed", user=user)
            _mfa_register_failure(request, user)
            return Response({"detail": _("Невірний код.")}, status=400)

        cache.delete(_mfa_fail_key(user))
        request.session.pop(PRE2FA_KEY, None)
        _complete_login(request, user, method)
        if method == "backup_code":
            remaining = user.backup_codes.filter(used_at__isnull=True).count()
            return Response({"status": "ok", "backup_codes_remaining": remaining})
        return Response({"status": "ok"})


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        key = request.session.session_key
        if request.user.is_authenticated:
            audit(request, "logout")
        logout(request)
        close_session_sockets([key])
        return Response({"status": "ok"})


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        invite = None
        token = data.get("invite") or ""
        if token:
            invite = Invite.objects.filter(token_hash=Invite.hash_token(token)).first()
            if invite is None or not invite.is_valid:
                raise ValidationError({"invite": [_("Запрошення недійсне або прострочене.")]})
            if invite.email and invite.email != data["email"]:
                raise ValidationError({"invite": [_("Запрошення видане на іншу адресу.")]})
        elif not settings.REGISTRATION_OPEN:
            raise PermissionDenied(_("Реєстрація можлива лише за запрошенням."))

        with transaction.atomic():
            extra = {}
            if invite and invite.quota_bytes is not None:
                extra["quota_bytes"] = invite.quota_bytes
            user = User.objects.create_user(data["username"], data["email"], data["password"], **extra)
            if invite:
                updated = Invite.objects.filter(pk=invite.pk, used_at__isnull=True).update(
                    used_at=timezone.now(), used_by=user
                )
                if updated != 1:
                    raise ValidationError({"invite": [_("Запрошення вже використано.")]})
        audit(request, "user.registered", user=user, invite=bool(invite))
        return Response({"status": "ok"}, status=201)


# ═══════════════════════════ Мій акаунт ═══════════════════════════


class ProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = ProfileSerializer

    def get_object(self):
        return self.request.user


class PreferencesView(generics.RetrieveUpdateAPIView):
    serializer_class = PreferencesSerializer

    def get_object(self):
        return Preferences.objects.get_or_create(user=self.request.user)[0]


class ChangePasswordView(APIView):
    def post(self, request):
        ser = ChangePasswordSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        _require_password(request, ser.validated_data["current_password"])
        user = request.user
        user.set_password(ser.validated_data["new_password"])
        user.save(update_fields=["password", "password_changed_at"])
        update_session_auth_hash(request, user)
        _revoke_other_sessions(request)
        audit(request, "password.changed")
        return Response({"status": "ok"})


class SecurityView(APIView):
    def get(self, request):
        user = request.user
        return Response(
            {
                "has_2fa": user.has_2fa,
                "backup_codes_remaining": user.backup_codes.filter(used_at__isnull=True).count(),
                "password_changed_at": user.password_changed_at,
                "require_2fa": bool(user.is_staff and settings.REQUIRE_2FA_FOR_STAFF),
            }
        )


class TotpSetupView(APIView):
    def post(self, request):
        ser = PasswordConfirmSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        _require_password(request, ser.validated_data["password"])
        if request.user.has_2fa:
            raise ValidationError({"detail": _("2FA вже увімкнено. Спочатку вимкніть її.")})
        secret, uri, qr = twofactor.start_totp_setup(request.user)
        audit(request, "2fa.setup_started")
        return Response({"secret": secret, "otpauth_uri": uri, "qr_svg": qr})


class TotpConfirmView(APIView):
    def post(self, request):
        ser = TwoFactorSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = User.objects.select_related("totp_device").get(pk=request.user.pk)
        codes = twofactor.confirm_totp(user, ser.validated_data["code"])
        if codes is None:
            raise ValidationError({"code": [_("Невірний код. Перевірте час на телефоні.")]})
        request.session["mfa"] = True
        _revoke_other_sessions(request)
        audit(request, "2fa.enabled")
        return Response({"backup_codes": codes})


class TwoFactorDisableView(APIView):
    def post(self, request):
        ser = DisableTwoFactorSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        _require_password(request, ser.validated_data["password"])
        if twofactor.verify_second_factor(request.user, ser.validated_data["code"]) is None:
            raise ValidationError({"code": [_("Невірний код.")]})
        twofactor.disable_2fa(request.user)
        request.session["mfa"] = False
        audit(request, "2fa.disabled")
        notify(request.user, "security", gettext_noop("Двофакторну автентифікацію вимкнено"))
        return Response({"status": "ok"})


class BackupCodesView(APIView):
    def post(self, request):
        ser = PasswordConfirmSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        _require_password(request, ser.validated_data["password"])
        if not request.user.has_2fa:
            raise ValidationError({"detail": _("Спочатку увімкніть 2FA.")})
        codes = twofactor.regenerate_backup_codes(request.user)
        audit(request, "2fa.backup_codes_regenerated")
        return Response({"backup_codes": codes})


def _revoke_other_sessions(request) -> int:
    current = request.session.session_key
    keys = list(
        UserSession.objects.filter(user=request.user).exclude(session_key=current).values_list("session_key", flat=True)
    )
    Session.objects.filter(session_key__in=keys).delete()
    UserSession.objects.filter(session_key__in=keys).delete()
    close_session_sockets(keys)
    return len(keys)


def _revoke_all_sessions(user) -> None:
    keys = list(UserSession.objects.filter(user=user).values_list("session_key", flat=True))
    Session.objects.filter(session_key__in=keys).delete()
    UserSession.objects.filter(user=user).delete()
    close_session_sockets(keys)
    close_user_sockets(user.pk)


class SessionsViewSet(viewsets.GenericViewSet, mixins.ListModelMixin):
    serializer_class = SessionSerializer
    pagination_class = None

    def get_queryset(self):
        live = Session.objects.filter(expire_date__gt=timezone.now()).values_list("session_key", flat=True)
        return UserSession.objects.filter(user=self.request.user, session_key__in=live).order_by("-last_seen")

    def destroy(self, request, pk=None):
        public_id = (pk or "").lower()
        if len(public_id) != 20:
            raise ValidationError({"detail": _("Невірний ідентифікатор.")})
        matches = [s for s in self.get_queryset() if session_public_id(s.session_key) == public_id]
        if len(matches) != 1:
            return Response(status=404)
        key = matches[0].session_key
        Session.objects.filter(session_key=key).delete()
        UserSession.objects.filter(session_key=key).delete()
        close_session_sockets([key])
        audit(request, "session.revoked", target=public_id)
        if key == request.session.session_key:
            logout(request)
        return Response(status=204)

    @action(detail=False, methods=["post"])
    def revoke_others(self, request):
        count = _revoke_other_sessions(request)
        audit(request, "session.revoked_others", count=count)
        return Response({"revoked": count})


class MyAuditLogView(APIView):
    def get(self, request):
        events = AuditLog.objects.filter(user=request.user).order_by("-created_at")[:100]
        return Response(
            [
                {"action": e.action, "created_at": e.created_at, "ip_address": e.ip_address, "user_agent": e.user_agent}
                for e in events
            ]
        )


# ═══════════════════════════ Пошук користувачів ═══════════════════════════


class UserSearchView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "user_search"

    def get(self, request):
        q = (request.query_params.get("q") or "").strip().lower()
        if len(q) < 2:
            return Response([])
        users = (
            User.objects.filter(is_active=True, preferences__discoverable=True)
            .filter(Q(username__startswith=q) | Q(display_name__icontains=q))
            .exclude(pk=request.user.pk)
            .order_by("username")[:10]
        )
        return Response(PublicUserSerializer(users, many=True).data)


# ═══════════════════════════ Адміністрування ═══════════════════════════


class AdminUserViewSet(
    viewsets.GenericViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin
):
    permission_classes = [IsStaffWith2FA]
    serializer_class = AdminUserSerializer

    def get_queryset(self):
        qs = User.objects.select_related("totp_device").order_by("username")
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(display_name__icontains=q))
        return qs

    def _check_target(self, target):
        """Звичайний адміністратор не керує іншими адміністраторами і суперкористувачами:
        інакше один скомпрометований staff-акаунт міг би вимкнути чи перехопити решту."""
        me = self.request.user
        if me.is_superuser or target == me:
            return
        if target.is_staff or target.is_superuser:
            raise PermissionDenied(_("Керувати адміністраторами може лише суперкористувач."))

    def perform_update(self, serializer):
        target = serializer.instance
        self._check_target(target)
        if target == self.request.user and serializer.validated_data.get("is_active") is False:
            raise ValidationError({"is_active": [_("Не можна деактивувати себе.")]})
        if "is_staff" in serializer.validated_data and not self.request.user.is_superuser:
            raise PermissionDenied(_("Лише суперкористувач може змінювати права адміністратора."))
        user = serializer.save()
        if user.is_active is False:
            _revoke_all_sessions(user)
        audit(self.request, "admin.user_updated", target=user.username, changes=list(serializer.validated_data))

    def create(self, request):
        ser = AdminCreateUserSerializer(data=request.data, context={"allow_reserved": request.user.is_superuser})
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        extra = {}
        if d.get("quota_gb") is not None:
            extra["quota_bytes"] = int(d["quota_gb"] * 1024**3)
        if d.get("is_staff"):
            if not request.user.is_superuser:
                raise PermissionDenied(_("Лише суперкористувач може створювати адміністраторів."))
            extra["is_staff"] = True
        user = User.objects.create_user(d["username"], d["email"], d["password"], **extra)
        audit(request, "admin.user_created", target=user.username)
        return Response(AdminUserSerializer(user).data, status=201)

    @action(detail=True, methods=["post"])
    def reset_2fa(self, request, pk=None):
        user = self.get_object()
        if user == request.user:
            raise ValidationError({"detail": _("Використайте власні налаштування безпеки.")})
        self._check_target(user)
        twofactor.disable_2fa(user)
        # Скидання 2FA зазвичай означає втрату телефону — завершуємо всі сесії користувача
        _revoke_all_sessions(user)
        audit(request, "admin.2fa_reset", target=user.username)
        notify(
            user,
            "security",
            gettext_noop("Адміністратор скинув вашу 2FA"),
            gettext_noop("Увімкніть її знову в налаштуваннях безпеки."),
        )
        return Response({"status": "ok"})

    @action(detail=False, methods=["get"])
    def stats(self, request):
        from apps.storage.models import File

        agg = User.objects.aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(is_active=True)),
            with_2fa=Count("id", filter=Q(totp_device__confirmed=True)),
            used=Sum("used_bytes"),
            allocated=Sum("quota_bytes"),
        )
        agg["files"] = File.objects.filter(deleted_at__isnull=True).count()
        agg["infected"] = File.objects.filter(status=File.Status.INFECTED).count()
        return Response(agg)


class AdminInviteViewSet(
    viewsets.GenericViewSet, mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin
):
    permission_classes = [IsStaffWith2FA]
    serializer_class = InviteSerializer
    queryset = Invite.objects.order_by("-created_at")

    def create(self, request):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        quota = int(d["quota_gb"] * 1024**3) if d.get("quota_gb") is not None else None
        invite, token = Invite.issue(
            created_by=request.user, email=d.get("email", ""), quota_bytes=quota, days=d.get("days", 7)
        )
        audit(request, "admin.invite_created", target=invite.email or str(invite.id))
        data = InviteSerializer(invite).data
        data["token"] = token  # показується лише один раз
        data["url"] = f"https://{settings.DOMAIN}/register?invite={token}"
        return Response(data, status=201)


class AdminAuditViewSet(viewsets.GenericViewSet, mixins.ListModelMixin):
    permission_classes = [IsStaffWith2FA]

    def get_queryset(self):
        qs = AuditLog.objects.select_related("user").order_by("-created_at")
        if action_ := self.request.query_params.get("action"):
            qs = qs.filter(action__startswith=action_)
        if user := self.request.query_params.get("user"):
            qs = qs.filter(user__username=user)
        return qs

    def list(self, request):
        page = self.paginate_queryset(self.get_queryset())
        data = [
            {
                "id": e.id,
                "created_at": e.created_at,
                "action": e.action,
                "user": e.user.username if e.user else None,
                "ip_address": e.ip_address,
                "target": e.target,
                "metadata": e.metadata,
            }
            for e in page
        ]
        return self.get_paginated_response(data)

