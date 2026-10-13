"""Адміністрування поштових скриньок: створення додаткових скриньок, перейменування,
ім'я відправника, квота, вимкнення, видалення.

Права перевіряються на сервері: IsStaffWith2FA (адмін + вхід з 2FA у цій сесії),
а скриньками інших адміністраторів керує лише суперкористувач."""

import logging

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import audit
from apps.core.permissions import IsStaffWith2FA
from apps.core.realtime import notify

from . import imap

from .models import LOCAL_PART_VALIDATOR, Alias, MailDomain, Mailbox

User = get_user_model()
MAX_MAILBOXES_PER_USER = 20
logger = logging.getLogger("blackcloud")


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


class DeleteMailboxSerializer(serializers.Serializer):
    password = serializers.CharField(max_length=1024, trim_whitespace=False)
    confirm = serializers.CharField(max_length=320)
    # Лише суперкористувач: видалити запис, навіть якщо листи не вдалося стерти через IMAP
    force = serializers.BooleanField(required=False, default=False)


def _list_folders(conn) -> list[tuple[str, str, bool]]:
    """[(назва, роздільник, можна_вибрати)] — усі теки скриньки, включно з \\Noselect."""
    typ, data = conn.list()
    if typ != "OK":
        raise imap.MailUnavailable()
    out = []
    for raw in data or []:
        if not isinstance(raw, bytes):
            continue
        m = imap.LIST_RE.match(raw)
        if not m:
            continue
        name = m.group("name").decode().strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        delim = m.group("delim").decode().strip('"')
        selectable = "\\noselect" not in m.group("flags").decode().lower()
        out.append((imap.decode_folder(name), delim if delim != "NIL" else "", selectable))
    return out


def wipe_mailbox(address: str) -> int:
    """Стирає всі листи й теки скриньки через master-сесію Dovecot. Повертає кількість листів.

    Будь-яка помилка IMAP — MailUnavailable (503): запис скриньки тоді не видаляється,
    щоб на диску не лишилося листів без власника."""
    removed = 0
    try:
        with imap.imap_session(address) as conn:
            folders = _list_folders(conn)
            for name, _delim, selectable in folders:
                if not selectable:
                    continue
                typ, data = conn.select(imap.quote(name), readonly=False)
                if typ != "OK":
                    raise imap.MailUnavailable()
                count = int((data or [b"0"])[0] or 0)
                if count:
                    typ, _r = conn.uid("STORE", "1:*", "+FLAGS.SILENT", "(\\Deleted)")
                    if typ != "OK":
                        raise imap.MailUnavailable()
                    typ, _r = conn.expunge()
                    if typ != "OK":
                        raise imap.MailUnavailable()
                    removed += count
                conn.close()
            # Спочатку найглибші теки; «Вхідні» не видаляються (їх не можна видалити в IMAP)
            nested = [f for f in folders if f[0].upper() != "INBOX"]
            nested.sort(key=lambda f: (f[0].count(f[1]) if f[1] else 0, len(f[0])), reverse=True)
            for name, _delim, _sel in nested:
                conn.unsubscribe(imap.quote(name))
                typ, _r = conn.delete(imap.quote(name))
                if typ != "OK":
                    logger.info("mailbox wipe: folder not deleted (%s)", address)
            typ, data = conn.select(imap.quote("INBOX"), readonly=True)
            if typ != "OK" or int((data or [b"0"])[0] or 0) != 0:
                raise imap.MailUnavailable()
    except imap.MailUnavailable:
        raise
    except (OSError, ValueError, imap.imaplib.IMAP4.error) as exc:
        raise imap.MailUnavailable() from exc
    return removed


class AdminMailboxDetailView(APIView):
    permission_classes = [IsStaffWith2FA]

    def delete(self, request, pk):
        """Видалити скриньку (користувач лишається): спершу листи через IMAP, потім записи."""
        from apps.accounts.models import is_reserved_username
        from apps.accounts.views import _require_password

        mb = Mailbox.objects.select_related("domain", "user").filter(pk=pk).first()
        if mb is None:
            raise NotFound()
        _check_target(request, mb.user)
        if is_reserved_username(mb.local_part) and not request.user.is_superuser:
            raise PermissionDenied(_("Службові скриньки видаляє лише суперкористувач."))
        ser = DeleteMailboxSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        address = mb.address
        if d["confirm"].strip().lower() != address:
            raise ValidationError({"confirm": [_("Введіть адресу скриньки для підтвердження.")]})
        if d["force"] and not request.user.is_superuser:
            raise PermissionDenied(_("Примусове видалення доступне лише суперкористувачу."))
        _require_password(request, d["password"])

        # Dovecot бачить лише активні скриньки — вимкнену на час очищення вмикаємо
        was_active = mb.active
        if not was_active:
            Mailbox.objects.filter(pk=mb.pk).update(active=True)
        try:
            removed = wipe_mailbox(address)
            wiped = True
        except imap.MailUnavailable as exc:
            if not was_active:
                Mailbox.objects.filter(pk=mb.pk).update(active=False)
            if not d["force"]:
                audit(request, "admin.mailbox_delete_failed", target=address)
                raise imap.MailUnavailable(
                    _("Не вдалося стерти листи через IMAP — скриньку не видалено. Спробуйте пізніше.")
                ) from exc
            removed, wiped = 0, False

        with transaction.atomic():
            aliases = Alias.objects.filter(destination=address) | Alias.objects.filter(source=address)
            alias_count = aliases.count()
            aliases.delete()
            mb.scheduled_messages.all().delete()
            try:
                blocked = django_apps.get_model("mail", "BlockedSender")
            except LookupError:
                blocked = None
            if blocked is not None and any(f.name == "mailbox" for f in blocked._meta.get_fields()):
                blocked.objects.filter(mailbox=mb).delete()
            mailbox_id = mb.pk
            mb.delete()
        cache.delete(f"push:mail:{mailbox_id}")
        audit(
            request,
            "admin.mailbox_deleted",
            target=address,
            owner=mb.user.username,
            messages=removed,
            aliases=alias_count,
            wiped=wiped,
        )
        notify(
            mb.user,
            "security",
            gettext_noop("Поштову скриньку видалено"),
            gettext_noop("Адміністратор видалив скриньку %(address)s разом з усіма листами."),
            "/mail",
            params={"address": address},
        )
        return Response(status=204)

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
