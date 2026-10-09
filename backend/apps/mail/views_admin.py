"""Адміністрування поштових скриньок: створення додаткових скриньок, перейменування,
ім'я відправника, квота, вимкнення.

Права перевіряються на сервері: IsStaffWith2FA (адмін + вхід з 2FA у цій сесії),
а скриньками інших адміністраторів керує лише суперкористувач."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import audit
from apps.core.permissions import IsStaffWith2FA

from .models import LOCAL_PART_VALIDATOR, Alias, MailDomain, Mailbox

User = get_user_model()
MAX_MAILBOXES_PER_USER = 20


def _check_target(request, user):
    me = request.user
    if me.is_superuser or user == me:
        return
    if user.is_staff or user.is_superuser:
        raise PermissionDenied(_("Скриньками адміністраторів керує лише суперкористувач."))


def _address_taken(local_part: str, domain: MailDomain, exclude_pk=None) -> bool:
    address = f"{local_part}@{domain.name}"
    qs = Mailbox.objects.filter(domain=domain, local_part=local_part)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists() or Alias.objects.filter(source=address).exists()


def _serialize(mb: Mailbox) -> dict:
    return {
        "id": str(mb.pk),
        "address": mb.address,
        "local_part": mb.local_part,
        "domain": mb.domain.name,
        "display_name": mb.display_name,
        "user": mb.user.username,
        "quota_mb": mb.quota_mb,
        "active": mb.active,
        "client_password_set": bool(mb.client_password_hash),
        "created_at": mb.created_at,
    }


def _domain(name: str | None) -> MailDomain:
    if name:
        domain = MailDomain.objects.filter(name=name.lower(), active=True).first()
        if domain is None:
            raise ValidationError({"domain": [_("Невідомий або вимкнений поштовий домен.")]})
        return domain
    return MailDomain.objects.get_or_create(name=settings.MAIL_DOMAIN.lower())[0]


class CreateMailboxSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=40)
    local_part = serializers.CharField(max_length=64, validators=[LOCAL_PART_VALIDATOR])
    domain = serializers.CharField(max_length=253, required=False, allow_blank=True)
    display_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    quota_mb = serializers.IntegerField(min_value=10, max_value=1_000_000, required=False)


class UpdateMailboxSerializer(serializers.Serializer):
    local_part = serializers.CharField(max_length=64, validators=[LOCAL_PART_VALIDATOR], required=False)
    display_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    quota_mb = serializers.IntegerField(min_value=10, max_value=1_000_000, required=False)
    active = serializers.BooleanField(required=False)
    keep_old_alias = serializers.BooleanField(required=False, default=True)


class AdminMailboxesView(APIView):
    permission_classes = [IsStaffWith2FA]

    def get(self, request):
        qs = Mailbox.objects.select_related("domain", "user").order_by("user__username", "created_at")
        if username := request.query_params.get("user"):
            qs = qs.filter(user__username=username.lower())
        return Response([_serialize(mb) for mb in qs[:1000]])

    def post(self, request):
        ser = CreateMailboxSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        user = User.objects.filter(username=d["username"].lower(), is_active=True).first()
        if user is None:
            raise ValidationError({"username": [_("Користувача не знайдено.")]})
        _check_target(request, user)
        if Mailbox.objects.filter(user=user).count() >= MAX_MAILBOXES_PER_USER:
            raise ValidationError({"detail": _("Максимум %(n)s скриньок на користувача.") % {"n": MAX_MAILBOXES_PER_USER}})
        domain = _domain(d.get("domain"))
        local_part = d["local_part"].lower()
        if _address_taken(local_part, domain):
            raise ValidationError({"local_part": [_("Ця адреса вже зайнята скринькою або псевдонімом.")]})
        try:
            with transaction.atomic():
                mb = Mailbox.objects.create(
                    user=user,
                    domain=domain,
                    local_part=local_part,
                    display_name=d.get("display_name", ""),
                    quota_mb=d.get("quota_mb") or settings.MAIL_DEFAULT_QUOTA_MB,
                )
        except IntegrityError as exc:
            raise ValidationError({"local_part": [_("Ця адреса вже зайнята.")]}) from exc
        audit(request, "admin.mailbox_created", target=mb.address, owner=user.username)
        return Response(_serialize(mb), status=201)


class AdminMailboxDetailView(APIView):
    permission_classes = [IsStaffWith2FA]

    def patch(self, request, pk):
        mb = Mailbox.objects.select_related("domain", "user").filter(pk=pk).first()
        if mb is None:
            raise NotFound()
        _check_target(request, mb.user)
        ser = UpdateMailboxSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        old_address = mb.address
        changes = []
        with transaction.atomic():
            if "local_part" in d and d["local_part"].lower() != mb.local_part:
                new_local = d["local_part"].lower()
                if _address_taken(new_local, mb.domain, exclude_pk=mb.pk):
                    raise ValidationError({"local_part": [_("Ця адреса вже зайнята скринькою або псевдонімом.")]})
                # Листи лежать у каталозі maildir, який не змінюється — перейменування безпечне
                mb.local_part = new_local
                changes.append("address")
            for field in ("display_name", "quota_mb", "active"):
                if field in d and getattr(mb, field) != d[field]:
                    setattr(mb, field, d[field])
                    changes.append(field)
            try:
                mb.save()
            except IntegrityError as exc:
                raise ValidationError({"local_part": [_("Ця адреса вже зайнята.")]}) from exc
            if "address" in changes and d.get("keep_old_alias", True):
                # Листи на стару адресу й далі приходять у цю скриньку
                Alias.objects.update_or_create(
                    source=old_address, defaults={"destination": mb.address, "active": True, "owner": mb.user}
                )
            if "address" in changes:
                Alias.objects.filter(destination=old_address).update(destination=mb.address)
        audit(request, "admin.mailbox_updated", target=mb.address, old_address=old_address, changes=changes)
        return Response(_serialize(mb))
