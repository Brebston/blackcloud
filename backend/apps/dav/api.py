"""API керування паролями застосунків WebDAV (/api/dav/passwords/)."""

from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext as _
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.views import _require_password
from apps.core.audit import audit

from . import auth
from .models import DavPassword


class DavPasswordSerializer(serializers.ModelSerializer):
    class Meta:
        model = DavPassword
        fields = ["id", "label", "created_at", "last_used_at", "last_used_ip"]


class CreateSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=60, allow_blank=True, required=False, default="")
    password = serializers.CharField(max_length=256, trim_whitespace=False)


def _server_url() -> str:
    return f"https://{settings.DOMAIN}/dav/"


class PasswordsView(APIView):
    def get(self, request):
        items = DavPassword.objects.filter(user=request.user)
        return Response(
            {
                "server_url": _server_url(),
                "username": request.user.username,
                "max": auth.MAX_PASSWORDS,
                "passwords": DavPasswordSerializer(items, many=True).data,
            }
        )

    def post(self, request):
        ser = CreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        _require_password(request, ser.validated_data["password"])
        label = " ".join(ser.validated_data["label"].split())[:60] or _("WebDAV")
        raw = auth.generate_password()
        with transaction.atomic():
            # Ліміт рахуємо під блокуванням рядка користувача (паралельні запити)
            User.objects.select_for_update().filter(pk=request.user.pk).first()
            if DavPassword.objects.filter(user=request.user).count() >= auth.MAX_PASSWORDS:
                raise ValidationError({"detail": _("Максимум %(n)s паролів WebDAV. Видаліть непотрібні.") % {"n": auth.MAX_PASSWORDS}})
            item = DavPassword.objects.create(user=request.user, label=label, password_hash=auth.hash_password(raw))
        audit(request, "dav.password_created", target=str(item.pk), label=label)
        data = DavPasswordSerializer(item).data
        data["password"] = raw  # показується лише один раз
        data["username"] = request.user.username
        data["server_url"] = _server_url()
        return Response(data, status=201)


class PasswordDetailView(APIView):
    def delete(self, request, pk):
        deleted, _count = DavPassword.objects.filter(pk=pk, user=request.user).delete()
        if not deleted:
            raise NotFound()
        audit(request, "dav.password_revoked", target=str(pk))
        return Response(status=204)
