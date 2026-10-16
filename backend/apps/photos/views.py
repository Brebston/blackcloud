"""API галереї «Фото»: стрічка за датою зйомки, деталі (EXIF, координати) й альбоми.

Видно лише власні файли користувача. Альбоми лише посилаються на файли.
"""

from datetime import datetime

from django.db import transaction
from django.db.models import Count, Max, Min, Q
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.storage.models import File

from . import indexing
from .models import Album, AlbumItem, PhotoMeta

PAGE_DEFAULT = 120
PAGE_MAX = 300
MAX_ALBUMS = 500
MAX_BATCH = 500


def _timeline(user):
    return (
        PhotoMeta.objects.filter(owner=user, file__owner=user, file__status=File.Status.CLEAN, file__deleted_at__isnull=True)
        .exclude(file__name__startswith="._")
        .select_related("file")
    )


def _item(row: PhotoMeta) -> dict:
    f = row.file
    return {
        # Ті самі поля, що й у «Файлах» (переглядач і мініатюри працюють без змін)
        "id": str(f.pk),
        "name": f.name,
        "folder": str(f.folder_id) if f.folder_id else None,
        "size": f.size,
        "mime_type": f.mime_type,
        "sha256": "",
        "status": f.status,
        "status_display": "",
        "scan_detail": "",
        "downloadable": f.is_downloadable,
        "has_thumbnail": f.has_thumbnail,
        "has_preview": f.has_preview,
        "content_version": f.content_version,
        "owner": "",
        "shared": None,
        "created_at": f.created_at,
        "updated_at": f.updated_at,
        "deleted_at": None,
        "photo": {
            "kind": row.kind,
            "sort_at": row.sort_at,
            "taken_at": row.taken_at,
            "width": row.width,
            "height": row.height,
            "duration": row.duration,
            "has_location": row.has_location,
        },
    }


def _cursor(value: str):
    """«<sort_at ISO>|<file id>» — стабільна пагінація стрічки."""
    if not value:
        return None
    stamp, _sep, file_id = value.partition("|")
    try:
        return datetime.fromisoformat(stamp), file_id
    except ValueError as exc:
        raise ValidationError({"cursor": ["invalid"]}) from exc


def _page(request, qs) -> Response:
    try:
        limit = max(1, min(int(request.query_params.get("limit") or PAGE_DEFAULT), PAGE_MAX))
    except ValueError:
        limit = PAGE_DEFAULT
    kind = request.query_params.get("kind")
    if kind in ("photo", "video"):
        qs = qs.filter(kind=kind)
    cur = _cursor(request.query_params.get("cursor", ""))
    total = None
    if cur is None:
        total = qs.count()
    else:
        stamp, file_id = cur
        qs = qs.filter(Q(sort_at__lt=stamp) | Q(sort_at=stamp, file_id__lt=file_id))
    rows = list(qs.order_by("-sort_at", "-file_id")[: limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = f"{rows[-1].sort_at.isoformat()}|{rows[-1].file_id}" if more and rows else None
    data = {"results": [_item(r) for r in rows], "next_cursor": nxt}
    if total is not None:
        data["total"] = total
        data["processing"] = qs.filter(status=PhotoMeta.Status.PENDING).count()
    return Response(data)


class TimelineView(APIView):
    """GET /api/photos/?cursor=&limit=&kind=photo|video — найновіші за датою зйомки першими."""

    def get(self, request):
        # Свіжі файли, які ще не потрапили в індекс (beat раз на 10 хв), — додаємо одразу
        missing = indexing.candidates().filter(owner=request.user, photo__isnull=True)
        if missing.exists():
            indexing.add_missing(2000)
        return _page(request, _timeline(request.user))


class PhotoDetailView(APIView):
    """Панель «Інформація»: дата зйомки, камера, об'єктив, розміри, координати (лише власнику)."""

    def get(self, request, pk):
        row = _timeline(request.user).filter(file_id=pk).first()
        if row is None:
            raise NotFound()
        data = _item(row)
        data["photo"].update(
            {
                "taken_source": row.taken_source,
                "camera_make": row.camera_make,
                "camera_model": row.camera_model,
                "lens": row.lens,
                "location": indexing.location_of(row),
                "status": row.status,
                "albums": [
                    {"id": str(a.pk), "name": a.name}
                    for a in Album.objects.filter(owner=request.user, items__file_id=pk).order_by("name")
                ],
            }
        )
        return Response(data)


# ─────────────────────────── Альбоми ───────────────────────────


class AlbumSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError(_("Вкажіть назву альбому."))
        return value


def _album_data(album: Album, user) -> dict:
    items = _timeline(user).filter(file__album_items__album=album)
    agg = items.aggregate(n=Count("pk"), first=Min("sort_at"), last=Max("sort_at"))
    cover = None
    if album.cover_id:
        cover = items.filter(file_id=album.cover_id).first()
    if cover is None:
        cover = items.order_by("-sort_at").first()
    return {
        "id": str(album.pk),
        "name": album.name,
        "count": agg["n"],
        "first_at": agg["first"],
        "last_at": agg["last"],
        "cover": _item(cover) if cover else None,
        "created_at": album.created_at,
        "updated_at": album.updated_at,
    }


def _album(request, pk) -> Album:
    album = Album.objects.filter(pk=pk, owner=request.user).first()
    if album is None:
        raise NotFound()
    return album


class AlbumsView(APIView):
    def get(self, request):
        return Response([_album_data(a, request.user) for a in Album.objects.filter(owner=request.user)])

    def post(self, request):
        if Album.objects.filter(owner=request.user).count() >= MAX_ALBUMS:
            raise ValidationError({"detail": _("Забагато альбомів (максимум %(n)s).") % {"n": MAX_ALBUMS}})
        ser = AlbumSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        album = Album.objects.create(owner=request.user, name=ser.validated_data["name"])
        ids = request.data.get("file_ids") or []
        if ids:
            _add_items(request, album, ids)
        return Response(_album_data(album, request.user), status=201)


class AlbumDetailView(APIView):
    def get(self, request, pk):
        album = _album(request, pk)
        return Response(_album_data(album, request.user))

    def patch(self, request, pk):
        album = _album(request, pk)
        if "name" in request.data:
            ser = AlbumSerializer(data={"name": request.data.get("name")})
            ser.is_valid(raise_exception=True)
            album.name = ser.validated_data["name"]
        if "cover" in request.data:
            cover = request.data.get("cover")
            if cover and not AlbumItem.objects.filter(album=album, file_id=cover).exists():
                raise ValidationError({"cover": [_("Обкладинка має бути з цього альбому.")]})
            album.cover_id = cover or None
        album.save()
        return Response(_album_data(album, request.user))

    def delete(self, request, pk):
        _album(request, pk).delete()  # файли лишаються на місці
        return Response(status=204)


def _file_ids(request) -> list[str]:
    ids = request.data.get("file_ids")
    if not isinstance(ids, list) or not ids or len(ids) > MAX_BATCH:
        raise ValidationError({"file_ids": [_("Передайте від 1 до %(n)s файлів.") % {"n": MAX_BATCH}]})
    return [str(i) for i in ids]


def _add_items(request, album: Album, ids) -> int:
    files = list(indexing.candidates().filter(owner=request.user, pk__in=[str(i) for i in ids][:MAX_BATCH]))
    with transaction.atomic():
        for f in files:
            indexing.ensure_row(f)
        AlbumItem.objects.bulk_create([AlbumItem(album=album, file=f) for f in files], ignore_conflicts=True)
        Album.objects.filter(pk=album.pk).update(updated_at=timezone.now())
    return len(files)


class AlbumItemsView(APIView):
    """GET — вміст альбому (як стрічка); POST {file_ids} — додати; DELETE {file_ids} — прибрати з альбому."""

    def get(self, request, pk):
        album = _album(request, pk)
        return _page(request, _timeline(request.user).filter(file__album_items__album=album))

    def post(self, request, pk):
        album = _album(request, pk)
        added = _add_items(request, album, _file_ids(request))
        return Response({"added": added, "album": _album_data(album, request.user)})

    def delete(self, request, pk):
        album = _album(request, pk)
        ids = _file_ids(request)
        removed, _x = AlbumItem.objects.filter(album=album, file_id__in=ids).delete()
        if album.cover_id and str(album.cover_id) in ids:
            album.cover = None
        album.save()
        return Response({"removed": removed, "album": _album_data(album, request.user)})
