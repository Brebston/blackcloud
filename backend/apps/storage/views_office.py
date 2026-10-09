import json
import logging

from django.conf import settings
from django.core import signing
from django.http import JsonResponse
from django.utils.translation import gettext as _
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Preferences
from apps.core.audit import audit
from apps.core.realtime import push_to_user

from . import office, services
from .models import File

logger = logging.getLogger("blackcloud")


def _internal_only(request):
    """file/ і callback/ викликає лише Document Server напряму (http://backend:8000).
    Ззовні (через Traefik, Host = домен) вони закриті і в Traefik, і тут."""
    host = request.get_host().split(":")[0]
    allowed = {"backend"} | ({"testserver"} if settings.TESTING else set())
    if host not in allowed:
        raise NotFound()


def _enabled():
    if not settings.OFFICE_ENABLED or not settings.OFFICE_JWT_SECRET:
        raise NotFound(_("Онлайн-редактор вимкнено."))


class OfficeConfigView(APIView):
    """Конфіг ONLYOFFICE для браузера (лише для користувачів з доступом до файлу)."""

    def get(self, request, pk):
        _enabled()
        f = services.get_readable_file(request.user, pk)
        if f.deleted_at is not None:
            raise NotFound(_("Файл у кошику."))
        if office.document_type(f.extension) is None:
            raise ValidationError({"detail": _("Цей формат не відкривається в редакторі.")})
        if not f.is_downloadable:
            raise ValidationError({"detail": _("Файл ще перевіряється антивірусом або заблокований.")})
        can_edit = (
            f.owner_id == request.user.pk
            and f.extension in office.EDITABLE
            and request.query_params.get("mode") != "view"
        )
        prefs = Preferences.objects.get_or_create(user=request.user)[0]
        theme = "theme-light" if prefs.theme == "light" else "theme-dark"
        config = office.build_config(f, request.user, can_edit=can_edit, language=prefs.language, theme=theme)
        audit(request, "office.opened", target=str(f.pk), mode=config["editorConfig"]["mode"])
        api_url = settings.OFFICE_PUBLIC_URL.rstrip("/") + "/web-apps/apps/api/documents/api.js"
        return Response({"config": config, "api_url": api_url})


class OfficeFileView(APIView):
    """Віддає файл Document Server'у (внутрішній запит, підписаний токен)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, pk):
        _enabled()
        _internal_only(request)
        try:
            data = office.read_file_token(request.query_params.get("t", ""))
        except signing.BadSignature:
            raise NotFound()
        if data.get("f") != str(pk):
            raise NotFound()
        f = File.objects.filter(pk=pk, deleted_at__isnull=True).first()
        if f is None or f.content_version != data.get("v") or not f.is_downloadable:
            raise NotFound()
        return services.file_response(f)


class OfficeCallbackView(APIView):
    """Callback від Document Server: статуси 2 (готово до збереження) і 6 (примусове збереження)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, pk):
        if not settings.OFFICE_ENABLED:
            return JsonResponse({"error": 1})
        _internal_only(request)
        try:
            ctx = office.read_callback_token(request.query_params.get("t", ""))
        except signing.BadSignature:
            return JsonResponse({"error": 1}, status=403)
        if ctx.get("f") != str(pk):
            return JsonResponse({"error": 1}, status=403)

        try:
            body = json.loads(request.body or b"{}")
        except ValueError:
            return JsonResponse({"error": 1}, status=400)
        token = body.get("token") or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        try:
            decoded = office.jwt_decode(token)
        except office.JWTError:
            logger.warning("office callback with invalid JWT for %s", pk)
            return JsonResponse({"error": 1}, status=403)
        payload = decoded.get("payload", decoded)

        status = payload.get("status")
        if status not in (2, 6):
            return JsonResponse({"error": 0})

        f = File.objects.filter(pk=pk, deleted_at__isnull=True).select_related("owner").first()
        if f is None or str(f.owner_id) != ctx.get("u"):
            return JsonResponse({"error": 1}, status=404)
        try:
            data = office.fetch_result(payload.get("url", ""))
            services.replace_content(f, data)
        except Exception as exc:
            logger.warning("office save failed for %s: %s", pk, exc)
            return JsonResponse({"error": 1})
        audit(request, "office.saved", user=f.owner, target=str(f.pk), size=len(data))
        push_to_user(f.owner_id, "file.updated", {"id": str(f.pk), "status": File.Status.SCANNING})
        return JsonResponse({"error": 0})
