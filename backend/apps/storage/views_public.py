"""Публічні посилання на теки і «скринька для файлів» (без входу в акаунт).

Після перевірки пароля (/api/public/authorize/) гість отримує «пропуск» (grant) — випадковий
рядок у кеші на GRANT_TTL. Він передається в тілі POST або заголовком X-Link-Grant, а не в
URL. Кожен запит знову перевіряє, що посилання дійсне (не відкликане, не прострочене,
тека не в кошику), тож відкликання діє одразу.

* Тека (kind=folder): перегляд підтек і файлів, мініатюри, перегляд у браузері (на
  usercontent-піддомені), завантаження файлу чи всього ZIP-архівом. Фото й відео — завжди
  без геолокації. Вміст поза текою посилання недоступний.
* Скринька (kind=drop): гість лише надсилає файли; вони лягають у підтеку з його іменем,
  займають квоту власника й проходять антивірус. Вмісту теки гість не бачить.
"""

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.audit import audit
from apps.core.realtime import notify
from apps.core.tickets import consume_ticket, issue_ticket, peek_ticket

from . import macmeta, services, zipstream
from .models import File, Folder, PublicLink
from .views import OctetStreamParser, link_target_ok

GRANT_TTL = 6 * 3600
UPLOAD_TTL = 24 * 3600
NOTIFY_EVERY = 10 * 60
MAX_SENDER = 60


def issue_grant(link: PublicLink) -> str:
    return issue_ticket("pl-grant", {"l": str(link.pk)}, GRANT_TTL)


def _grant(request) -> str:
    value = request.META.get("HTTP_X_LINK_GRANT") or (request.data.get("grant") if hasattr(request, "data") else "")
    return value if isinstance(value, str) else ""


def link_from_grant(request, kind: str) -> PublicLink:
    data = peek_ticket("pl-grant", _grant(request))
    if data is None:
        raise PermissionDenied(_("Сесія посилання завершилась. Відкрийте посилання знову."))
    link = PublicLink.objects.select_related("folder", "owner").filter(pk=data["l"], kind=kind).first()
    if not link_target_ok(link):
        raise NotFound(_("Посилання недійсне або прострочене."))
    return link


def _inside(link: PublicLink, folder: Folder) -> bool:
    return any(a.pk == link.folder_id for a in services.ancestors(folder))


def folder_in_link(link: PublicLink, folder_id) -> Folder:
    if folder_id in (None, "", "root"):
        return link.folder
    try:
        folder = Folder.objects.filter(pk=folder_id, owner_id=link.owner_id, deleted_at__isnull=True).first()
    except (ValueError, TypeError):
        folder = None
    if folder is None or services.is_in_trash(folder) or not _inside(link, folder):
        raise NotFound(_("Папку не знайдено."))
    return folder


def file_in_link(link: PublicLink, file_id) -> File:
    try:
        f = File.objects.select_related("folder").filter(pk=file_id, owner_id=link.owner_id, deleted_at__isnull=True).first()
    except (ValueError, TypeError):
        f = None
    if f is None or f.folder is None or not f.is_downloadable or macmeta.is_system_name(f.name):
        raise NotFound(_("Файл не знайдено."))
    if services.is_in_trash(f.folder) or not _inside(link, f.folder):
        raise NotFound(_("Файл не знайдено."))
    return f


class PublicFolderView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_browse"


def _file_data(f: File) -> dict:
    return {
        "id": str(f.pk),
        "name": f.name,
        "size": f.size,
        "mime_type": f.mime_type,
        "has_thumbnail": f.has_thumbnail,
        "has_preview": f.has_preview,
        "content_version": f.content_version,
        "updated_at": f.content_updated_at or f.updated_at,
        "inline": f.mime_type in services.SAFE_INLINE_MIME,
    }


class FolderListView(PublicFolderView):
    """POST {grant, folder?} — вміст теки (лише в межах теки посилання)."""

    def post(self, request):
        link = link_from_grant(request, PublicLink.Kind.FOLDER)
        folder = folder_in_link(link, request.data.get("folder"))
        chain = services.ancestors(folder)
        start = next(i for i, a in enumerate(chain) if a.pk == link.folder_id)
        folders = Folder.objects.filter(owner_id=link.owner_id, parent=folder, deleted_at__isnull=True).order_by("name")
        files = (
            macmeta.hide_system(File.objects.filter(owner_id=link.owner_id, folder=folder, deleted_at__isnull=True))
            .exclude(status=File.Status.UPLOADING)
            .order_by("name")
        )
        return Response(
            {
                "folder": {"id": str(folder.pk), "name": folder.name},
                "breadcrumbs": [{"id": str(a.pk), "name": a.name} for a in chain[start:]],
                "folders": [{"id": str(c.pk), "name": c.name} for c in folders],
                "files": [_file_data(f) for f in files if f.is_downloadable],
            }
        )


class FolderImageView(PublicFolderView):
    """POST {grant, file, size: thumb|preview} → WebP (створене нашим кодувальником, без EXIF)."""

    throttle_scope = "public_thumb"

    def post(self, request):
        link = link_from_grant(request, PublicLink.Kind.FOLDER)
        f = file_in_link(link, request.data.get("file"))
        if request.data.get("size") == "preview":
            if not f.has_preview:
                raise NotFound()
            data = services.read_preview_image(f)
        else:
            if not f.has_thumbnail:
                raise NotFound()
            data = services.read_thumbnail(f)
        response = HttpResponse(data, content_type="image/webp")
        response["Cache-Control"] = "private, max-age=3600"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response


class FolderFileTicketView(PublicFolderView):
    """POST {grant, file, mode: download|view} → одноразова адреса завантаження або перегляду."""

    def post(self, request):
        link = link_from_grant(request, PublicLink.Kind.FOLDER)
        f = file_in_link(link, request.data.get("file"))
        if request.data.get("mode") == "view":
            if f.mime_type not in services.SAFE_INLINE_MIME:
                raise ValidationError({"detail": _("Цей тип файлу не переглядається в браузері.")})
            ticket = issue_ticket("preview", {"pl": str(link.pk), "f": str(f.pk), "v": f.content_version}, settings.PREVIEW_TICKET_TTL)
            return Response({"url": f"https://{settings.USERCONTENT_HOST}/api/preview/{ticket}/"})
        ticket = issue_ticket("public-dl", {"link": str(link.pk), "file": str(f.pk)}, settings.DOWNLOAD_TICKET_TTL)
        return Response({"download_url": f"/api/public/dl/{ticket}/"})


class FolderZipTicketView(PublicFolderView):
    """POST {grant, folder?} → одноразова адреса ZIP-архіву теки (або підтеки)."""

    def post(self, request):
        link = link_from_grant(request, PublicLink.Kind.FOLDER)
        folder = folder_in_link(link, request.data.get("folder"))
        plan = zipstream.build_plan(folders=[folder])  # перевіряє ліміти розміру й кількості
        ticket = issue_ticket("public-zip", {"link": str(link.pk), "folder": str(folder.pk)}, settings.DOWNLOAD_TICKET_TTL)
        return Response({"download_url": f"/api/public/zip/{ticket}/", "files": sum(1 for e in plan.entries if e.file), "bytes": plan.total_bytes})


class FolderZipView(PublicFolderView):
    def get(self, request, ticket):
        data = consume_ticket("public-zip", ticket)
        if data is None:
            raise PermissionDenied(_("Посилання для завантаження прострочене. Оновіть сторінку."))
        link = PublicLink.objects.select_related("folder", "owner").filter(pk=data["link"], kind=PublicLink.Kind.FOLDER).first()
        if not link_target_ok(link):
            raise NotFound(_("Посилання недійсне або прострочене."))
        folder = folder_in_link(link, data["folder"])
        plan = zipstream.build_plan(folders=[folder])
        PublicLink.objects.filter(pk=link.pk).update(download_count=F("download_count") + 1)
        audit(request, "public_link.zip", user=link.owner, target=str(link.pk), files=sum(1 for e in plan.entries if e.file))
        return zipstream.zip_response(plan, f"{zipstream.safe_component(folder.name)}.zip", scrub_location=True)


# ─────────────────────────── Скринька для файлів ───────────────────────────


class DropStartSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=1024)
    size = serializers.IntegerField(min_value=0)
    sender = serializers.CharField(max_length=MAX_SENDER)


def _sender_folder(link: PublicLink, sender: str) -> Folder:
    """Підтека з ім'ям відправника в теці скриньки (створюється за потреби)."""
    name = services.clean_name(sender)
    folder = Folder.objects.filter(owner_id=link.owner_id, parent=link.folder, name=name, deleted_at__isnull=True).first()
    if folder is None:
        folder = Folder.objects.create(owner_id=link.owner_id, parent=link.folder, name=name)
    return folder


class DropStartView(PublicFolderView):
    throttle_scope = "public_upload"

    def post(self, request):
        link = link_from_grant(request, PublicLink.Kind.DROP)
        ser = DropStartSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        sender = " ".join(d["sender"].split())
        if len(sender) < 2:
            raise ValidationError({"sender": [_("Вкажіть своє ім'я.")]})
        size = d["size"]
        # Ліміт посилання (якщо задано) — атомарно, щоб паралельні завантаження його не перевищили
        q = PublicLink.objects.filter(pk=link.pk)
        if link.max_upload_bytes is not None:
            q = q.filter(upload_bytes__lte=link.max_upload_bytes - size)
        if q.update(upload_bytes=F("upload_bytes") + size) != 1:
            raise PermissionDenied(_("Ліміт цієї скриньки вичерпано."))
        try:
            with transaction.atomic():
                folder = _sender_folder(link, sender)
                f = services.start_upload(link.owner, name=d["name"], size=size, folder=folder)
        except Exception:
            PublicLink.objects.filter(pk=link.pk).update(upload_bytes=F("upload_bytes") - size)
            raise
        upload = issue_ticket("pl-upload", {"l": str(link.pk), "f": str(f.pk), "s": sender}, UPLOAD_TTL)
        return Response({"upload": upload, "name": f.name, "chunk_size": f.chunk_size, "chunks_total": f.chunks_total}, status=201)


def _upload(request, link: PublicLink):
    token = request.META.get("HTTP_X_UPLOAD_TOKEN") or (request.data.get("upload") if isinstance(request.data, dict) else "")
    data = peek_ticket("pl-upload", token if isinstance(token, str) else "")
    if data is None or data["l"] != str(link.pk):
        raise PermissionDenied(_("Сесія завантаження завершилась. Почніть знову."))
    f = File.objects.select_related("owner").filter(pk=data["f"], owner_id=link.owner_id).first()
    if f is None:
        raise NotFound()
    return f, data


class DropChunkView(PublicFolderView):
    parser_classes = [OctetStreamParser]
    throttle_scope = "public_upload"

    def put(self, request, index):
        link = link_from_grant(request, PublicLink.Kind.DROP)
        f, _data = _upload(request, link)
        data = request.data if isinstance(request.data, bytes | bytearray) else b""
        f = services.write_chunk(f, int(index), bytes(data))
        return Response({"next_index": f.chunks_received, "chunks_total": f.chunks_total})


class DropCompleteView(PublicFolderView):
    throttle_scope = "public_upload"

    def post(self, request):
        link = link_from_grant(request, PublicLink.Kind.DROP)
        f, data = _upload(request, link)
        with transaction.atomic():
            f = services.complete_upload(f)
        PublicLink.objects.filter(pk=link.pk).update(upload_count=F("upload_count") + 1)
        audit(request, "public_link.dropped", user=link.owner, target=str(link.pk), file=str(f.pk), size=f.size, sender=data["s"])
        # Власнику — одне сповіщення на відправника раз на 10 хв (а не на кожен із сотні файлів)
        if cache.add(f"drop-notify:{link.pk}:{data['s'].lower()}", 1, NOTIFY_EVERY):
            notify(
                link.owner,
                "files",
                gettext_noop("%(sender)s надсилає файли"),
                gettext_noop("У скриньку «%(folder)s» надходять файли (перший: %(name)s)."),
                f"/files?folder={f.folder_id}",
                params={"sender": data["s"], "folder": link.folder.name, "name": f.name},
            )
        return Response({"name": f.name, "size": f.size, "status": f.status, "at": timezone.now()})
