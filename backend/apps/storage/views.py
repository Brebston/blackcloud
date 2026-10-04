from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import BaseParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.audit import audit
from apps.core.realtime import notify

from . import services
from .models import File, Folder, PublicLink, Share
from .serializers import (
    CreateFolderSerializer,
    CreatePublicLinkSerializer,
    CreateShareSerializer,
    FileSerializer,
    FolderSerializer,
    PublicLinkSerializer,
    RenameMoveSerializer,
    ShareSerializer,
    StartUploadSerializer,
)

User = get_user_model()
DOWNLOAD_SIGNER_SALT = "bc-public-download"


def _visible_files(qs):
    return qs.filter(deleted_at__isnull=True).exclude(status=File.Status.UPLOADING)


class OctetStreamParser(BaseParser):
    media_type = "application/octet-stream"

    def parse(self, stream, media_type=None, parser_context=None):
        request = parser_context["request"]
        limit = settings.UPLOAD_CHUNK_SIZE
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length > limit:
            raise ValidationError({"detail": "Чанк завеликий."})
        if stream is None:
            return b""
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValidationError({"detail": "Чанк завеликий."})
        return data


# ═══════════════════════════ Перегляд ═══════════════════════════


class BrowseView(APIView):
    """Вміст папки: власної або доступної через спільний доступ (лише читання)."""

    def get(self, request):
        folder_id = request.query_params.get("folder") or "root"
        user = request.user
        writable = True
        if folder_id == "root":
            folder = None
            owner = user
        else:
            folder = Folder.objects.select_related("parent").filter(pk=folder_id, deleted_at__isnull=True).first()
            if folder is None:
                raise NotFound("Папку не знайдено.")
            if folder.owner_id == user.pk:
                if services.is_in_trash(folder):
                    raise NotFound("Папку не знайдено.")
                owner = user
            else:
                share = services.shared_folder_root(user, folder)
                if share is None:
                    raise NotFound("Папку не знайдено.")
                owner = folder.owner
                writable = False

        folders = Folder.objects.filter(owner=owner, parent=folder, deleted_at__isnull=True)
        files = _visible_files(File.objects.filter(owner=owner, folder=folder)).annotate(
            share_count=Count("shares", distinct=True) + Count("public_links", distinct=True)
        )
        if writable:
            breadcrumbs = [{"id": f.id, "name": f.name} for f in services.ancestors(folder)]
        else:
            # Для спільної папки показуємо шлях лише від кореня спільного доступу
            chain = services.ancestors(folder)
            share_root_ids = set(
                Share.objects.filter(recipient=user, folder_id__in=[f.pk for f in chain]).values_list("folder_id", flat=True)
            )
            start = next(i for i, f in enumerate(chain) if f.pk in share_root_ids)
            breadcrumbs = [{"id": f.id, "name": f.name} for f in chain[start:]]

        return Response(
            {
                "folder": FolderSerializer(folder).data if folder else None,
                "writable": writable,
                "owner": owner.username,
                "breadcrumbs": breadcrumbs,
                "folders": FolderSerializer(folders, many=True).data,
                "files": FileSerializer(files, many=True).data,
            }
        )


class SearchView(APIView):
    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        if len(q) < 2:
            return Response({"folders": [], "files": []})
        folders = Folder.objects.filter(owner=request.user, deleted_at__isnull=True, name__icontains=q)[:50]
        files = _visible_files(File.objects.filter(owner=request.user, name__icontains=q))[:100]
        return Response(
            {
                "folders": FolderSerializer([f for f in folders if not services.is_in_trash(f)], many=True).data,
                "files": FileSerializer(
                    [f for f in files if f.folder is None or not services.is_in_trash(f.folder)], many=True
                ).data,
            }
        )


class UsageView(APIView):
    def get(self, request):
        user = User.objects.get(pk=request.user.pk)
        base = File.objects.filter(owner=user).exclude(status=File.Status.UPLOADING)
        trash = base.filter(deleted_at__isnull=False).aggregate(s=Sum("size"))["s"] or 0
        by_type = {}
        for row in base.filter(deleted_at__isnull=True).values("mime_type").annotate(s=Sum("size")):
            group = row["mime_type"].split("/")[0]
            by_type[group] = by_type.get(group, 0) + row["s"]
        return Response(
            {"quota_bytes": user.quota_bytes, "used_bytes": user.used_bytes, "trash_bytes": trash, "by_type": by_type}
        )


# ═══════════════════════════ Папки ═══════════════════════════


class FolderCreateView(APIView):
    def post(self, request):
        ser = CreateFolderSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        parent = services.get_own_folder(request.user, ser.validated_data.get("parent"))
        if parent is not None and len(services.ancestors(parent)) >= services.MAX_DEPTH:
            raise ValidationError({"parent": ["Надто велика вкладеність папок."]})
        name = services.clean_name(ser.validated_data["name"])
        with transaction.atomic():
            name = services.unique_name(request.user, parent, name)
            folder = Folder.objects.create(owner=request.user, parent=parent, name=name)
        audit(request, "folder.created", target=str(folder.pk))
        return Response(FolderSerializer(folder).data, status=201)


class FolderDetailView(APIView):
    def patch(self, request, pk):
        folder = services.get_own_folder(request.user, pk)
        ser = RenameMoveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        with transaction.atomic():
            if "target" in d:
                new_parent = services.get_own_folder(request.user, d["target"])
                services.assert_not_descendant(folder, new_parent)
                folder.parent = new_parent
            if "name" in d:
                folder.name = services.clean_name(d["name"])
            folder.name = services.unique_name(request.user, folder.parent, folder.name, exclude_folder=folder)
            folder.save()
        audit(request, "folder.updated", target=str(folder.pk))
        return Response(FolderSerializer(folder).data)

    def delete(self, request, pk):
        folder = services.get_own_folder(request.user, pk)
        folder.deleted_at = timezone.now()
        folder.save(update_fields=["deleted_at"])
        audit(request, "folder.trashed", target=str(folder.pk))
        return Response(status=204)


# ═══════════════════════════ Файли ═══════════════════════════


class FileDetailView(APIView):
    def get(self, request, pk):
        f = services.get_readable_file(request.user, pk)
        return Response(FileSerializer(f).data)

    def patch(self, request, pk):
        f = services.get_own_file(request.user, pk)
        ser = RenameMoveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        with transaction.atomic():
            if "target" in d:
                f.folder = services.get_own_folder(request.user, d["target"])
            if "name" in d:
                f.name = services.clean_name(d["name"])
            f.name = services.unique_name(request.user, f.folder, f.name, exclude_file=f)
            f.save(update_fields=["folder", "name", "updated_at"])
        audit(request, "file.updated", target=str(f.pk))
        return Response(FileSerializer(f).data)

    def delete(self, request, pk):
        f = services.get_own_file(request.user, pk)
        if f.status == File.Status.UPLOADING:
            services.purge_file(f)
        else:
            f.deleted_at = timezone.now()
            f.save(update_fields=["deleted_at"])
        audit(request, "file.trashed", target=str(f.pk))
        return Response(status=204)


class FileDownloadView(APIView):
    def get(self, request, pk):
        f = services.get_readable_file(request.user, pk)
        if f.deleted_at is not None:
            raise NotFound("Файл у кошику.")
        inline = request.query_params.get("inline") == "1"
        if f.owner_id != request.user.pk:
            audit(request, "file.shared_download", target=str(f.pk))
        return services.file_response(f, inline=inline)


# ═══════════════════════════ Завантаження чанками ═══════════════════════════


class UploadStartView(APIView):
    def post(self, request):
        ser = StartUploadSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        folder = services.get_own_folder(request.user, ser.validated_data.get("folder"))
        f = services.start_upload(
            request.user, name=ser.validated_data["name"], size=ser.validated_data["size"], folder=folder
        )
        return Response(
            {"id": f.id, "name": f.name, "chunk_size": f.chunk_size, "chunks_total": f.chunks_total, "next_index": 0},
            status=201,
        )


class UploadDetailView(APIView):
    def get(self, request, pk):
        f = services.get_own_file(request.user, pk)
        return Response(
            {"id": f.id, "status": f.status, "next_index": f.chunks_received, "chunks_total": f.chunks_total}
        )

    def delete(self, request, pk):
        f = services.get_own_file(request.user, pk)
        if f.status != File.Status.UPLOADING:
            raise ValidationError({"detail": "Завантаження вже завершено."})
        services.purge_file(f)
        return Response(status=204)


class UploadChunkView(APIView):
    parser_classes = [OctetStreamParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "upload_chunk"

    def put(self, request, pk, index):
        f = services.get_own_file(request.user, pk)
        data = request.data if isinstance(request.data, bytes | bytearray) else b""
        f = services.write_chunk(f, int(index), bytes(data))
        return Response({"next_index": f.chunks_received, "chunks_total": f.chunks_total})


class UploadCompleteView(APIView):
    def post(self, request, pk):
        f = services.get_own_file(request.user, pk)
        with transaction.atomic():
            f = services.complete_upload(f)
        audit(request, "file.uploaded", target=str(f.pk), size=f.size)
        return Response(FileSerializer(f).data)


# ═══════════════════════════ Кошик ═══════════════════════════


class TrashView(APIView):
    def get(self, request):
        user = request.user
        folders = Folder.objects.filter(owner=user, deleted_at__isnull=False).order_by("-deleted_at")
        files = File.objects.filter(owner=user, deleted_at__isnull=False).order_by("-deleted_at")
        return Response(
            {
                "retention_days": settings.TRASH_RETENTION.days,
                "folders": FolderSerializer(folders, many=True).data,
                "files": FileSerializer(files, many=True).data,
            }
        )


class TrashActionView(APIView):
    def _get(self, request):
        kind = request.data.get("type")
        pk = request.data.get("id")
        if kind == "file":
            f = File.objects.filter(pk=pk, owner=request.user, deleted_at__isnull=False).first()
            if f is None:
                raise NotFound()
            return kind, f
        if kind == "folder":
            folder = Folder.objects.filter(pk=pk, owner=request.user, deleted_at__isnull=False).first()
            if folder is None:
                raise NotFound()
            return kind, folder
        raise ValidationError({"type": ["file або folder"]})

    def post(self, request, op):
        if op == "empty":
            count = 0
            for f in File.objects.filter(owner=request.user, deleted_at__isnull=False).select_related("owner"):
                services.purge_file(f)
                count += 1
            for folder in Folder.objects.filter(owner=request.user, deleted_at__isnull=False):
                if Folder.objects.filter(pk=folder.pk).exists():
                    count += services.purge_folder(folder)
            audit(request, "trash.emptied", count=count)
            return Response({"purged": count})

        kind, obj = self._get(request)
        if op == "restore":
            with transaction.atomic():
                parent = obj.folder if kind == "file" else obj.parent
                if parent is not None and services.is_in_trash(parent):
                    # Батьківська папка теж у кошику — відновлюємо в корінь
                    parent = None
                    if kind == "file":
                        obj.folder = None
                    else:
                        obj.parent = None
                obj.name = services.unique_name(
                    request.user, parent, obj.name, **({"exclude_file": obj} if kind == "file" else {"exclude_folder": obj})
                )
                obj.deleted_at = None
                obj.save()
            audit(request, f"{kind}.restored", target=str(obj.pk))
            return Response({"status": "ok"})
        if op == "purge":
            if kind == "file":
                services.purge_file(obj)
            else:
                services.purge_folder(obj)
            audit(request, f"{kind}.purged", target=str(obj.pk))
            return Response(status=204)
        raise NotFound()


# ═══════════════════════════ Спільний доступ ═══════════════════════════


class SharesView(APIView):
    def get(self, request):
        shares = Share.objects.filter(owner=request.user).select_related("recipient", "file", "folder", "owner")
        return Response(ShareSerializer(shares, many=True).data)

    def post(self, request):
        ser = CreateShareSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        recipient = User.objects.filter(username=d["username"].lower(), is_active=True).first()
        if recipient is None or recipient == request.user:
            raise ValidationError({"username": ["Користувача не знайдено."]})
        target = {}
        if d.get("file"):
            f = services.get_own_file(request.user, d["file"])
            if f.status == File.Status.INFECTED:
                raise ValidationError({"file": ["Заражений файл не можна поширювати."]})
            target["file"] = f
            name = f.name
        else:
            folder = services.get_own_folder(request.user, d["folder"])
            if folder is None:
                raise ValidationError({"folder": ["Не можна поширити кореневу папку."]})
            target["folder"] = folder
            name = folder.name
        share, created = Share.objects.get_or_create(owner=request.user, recipient=recipient, **target)
        if created:
            audit(request, "share.created", target=str(share.pk), recipient=recipient.username)
            notify(
                recipient,
                "share",
                f"{request.user.display_name or request.user.username} поділився з вами",
                name,
                "/shared",
            )
        return Response(ShareSerializer(share).data, status=201 if created else 200)


class ShareDetailView(APIView):
    def delete(self, request, pk):
        share = Share.objects.filter(pk=pk).filter(Q(owner=request.user) | Q(recipient=request.user)).first()
        if share is None:
            raise NotFound()
        share.delete()
        audit(request, "share.deleted", target=str(pk))
        return Response(status=204)


class SharedWithMeView(APIView):
    def get(self, request):
        shares = (
            Share.objects.filter(recipient=request.user)
            .select_related("owner", "file", "folder", "recipient")
            .filter(Q(file__deleted_at__isnull=True, file__isnull=False) | Q(folder__deleted_at__isnull=True, folder__isnull=False))
        )
        result = []
        for s in shares:
            item = ShareSerializer(s).data
            if s.file_id:
                if not s.file.is_downloadable and s.file.status != File.Status.SCANNING:
                    continue
                item["file_info"] = FileSerializer(s.file).data
            result.append(item)
        return Response(result)


# ═══════════════════════════ Публічні посилання ═══════════════════════════


class PublicLinksView(APIView):
    def get(self, request):
        links = PublicLink.objects.filter(owner=request.user, revoked_at__isnull=True).select_related("file")
        file_id = request.query_params.get("file")
        if file_id:
            links = links.filter(file_id=file_id)
        return Response(PublicLinkSerializer(links.order_by("-created_at"), many=True).data)

    def post(self, request):
        ser = CreatePublicLinkSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        f = services.get_own_file(request.user, d["file"])
        if not f.is_downloadable:
            raise ValidationError({"file": ["Файл ще не перевірено або заблоковано."]})
        token = PublicLink.new_token()
        link = PublicLink.objects.create(
            owner=request.user,
            file=f,
            token_hash=PublicLink.hash_token(token),
            password_hash=make_password(d["password"]) if d.get("password") else "",
            expires_at=services.public_link_expiry(d["expires_days"]),
            max_downloads=d.get("max_downloads"),
        )
        audit(request, "public_link.created", target=str(f.pk), expires=link.expires_at.isoformat())
        data = PublicLinkSerializer(link).data
        data["url"] = f"https://{settings.DOMAIN}/s/{token}"  # показується лише один раз
        return Response(data, status=201)


class PublicLinkDetailView(APIView):
    def delete(self, request, pk):
        updated = PublicLink.objects.filter(pk=pk, owner=request.user, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
        if not updated:
            raise NotFound()
        audit(request, "public_link.revoked", target=str(pk))
        return Response(status=204)


def _get_active_link(token: str) -> PublicLink:
    link = (
        PublicLink.objects.select_related("file")
        .filter(token_hash=PublicLink.hash_token(token or ""))
        .first()
    )
    if link is None or not link.is_active or link.file.deleted_at is not None or not link.file.is_downloadable:
        raise NotFound("Посилання недійсне або прострочене.")
    return link


class PublicLinkInfoView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_link"

    def get(self, request, token):
        link = _get_active_link(token)
        return Response(
            {
                "name": link.file.name,
                "size": link.file.size,
                "mime_type": link.file.mime_type,
                "requires_password": bool(link.password_hash),
                "expires_at": link.expires_at,
            }
        )


class PublicLinkAuthorizeView(APIView):
    """Перевіряє пароль і видає короткоживучий підписаний URL для завантаження."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_link"

    def post(self, request, token):
        link = _get_active_link(token)
        if link.password_hash and not check_password(request.data.get("password") or "", link.password_hash):
            audit(request, "public_link.bad_password", user=link.owner, target=str(link.pk))
            return Response({"detail": "Невірний пароль."}, status=403)
        sig = signing.TimestampSigner(salt=DOWNLOAD_SIGNER_SALT).sign(str(link.pk))
        return Response({"download_url": f"/api/public/{token}/download/?sig={sig}"})


class PublicLinkDownloadView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_link"

    def get(self, request, token):
        link = _get_active_link(token)
        try:
            link_id = signing.TimestampSigner(salt=DOWNLOAD_SIGNER_SALT).unsign(
                request.query_params.get("sig", ""), max_age=120
            )
        except signing.BadSignature:
            raise PermissionDenied("Посилання для завантаження прострочене. Оновіть сторінку.")
        if link_id != str(link.pk):
            raise PermissionDenied()
        # Атомарний лічильник з перевіркою ліміту
        q = PublicLink.objects.filter(pk=link.pk)
        if link.max_downloads is not None:
            q = q.filter(download_count__lt=link.max_downloads)
        if q.update(download_count=F("download_count") + 1) != 1:
            raise NotFound("Ліміт завантажень вичерпано.")
        audit(request, "public_link.download", user=link.owner, target=str(link.pk))
        return services.file_response(link.file)
