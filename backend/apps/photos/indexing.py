"""Індексація фото й відео для стрічки: дата зйомки, камера, розміри, координати.

* Нові медіафайли спершу потрапляють у стрічку за датою завантаження (статус pending),
  потім worker читає метадані (в ізольованому thumbnailer) і переставляє їх на дату зйомки.
* Джерела дати: EXIF / теги відео → ім'я файлу (IMG_20241014_120001, 2024-10-14 12.00.01) →
  дата завантаження.
* EXIF-час без зміщення трактується в часовому поясі власника (Налаштування → Профіль).
"""

import json
import logging
import re
import urllib.request
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from apps.core import crypto as field_crypto
from apps.storage import thumbnails, thumbrender
from apps.storage.models import File

from .models import PhotoMeta

logger = logging.getLogger("blackcloud")
MAX_ATTEMPTS = 3
META_MAX_BYTES = 64 * 1024


def media_q() -> Q:
    return Q(mime_type__in=thumbnails.IMAGE_MIME) | Q(mime_type__in=thumbnails.VIDEO_MIME)


def kind_of(f: File) -> str | None:
    if f.mime_type in thumbnails.IMAGE_MIME:
        return "photo"
    if f.mime_type in thumbnails.VIDEO_MIME:
        return "video"
    return None


def candidates():
    """Файли, які мають бути в стрічці: свої, перевірені антивірусом, не в кошику, не службові ._."""
    return File.objects.filter(media_q(), status=File.Status.CLEAN, deleted_at__isnull=True).exclude(name__startswith="._")


# ─────────────────────────── Читання метаданих ───────────────────────────


def _post_json(kind: str, body, length: int) -> dict:
    url = settings.THUMBNAILER_URL.rstrip("/") + f"/meta?kind={kind}"
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/octet-stream", "Content-Length": str(length)}
    )
    with urllib.request.urlopen(request, timeout=180) as resp:  # noqa: S310 — адреса з налаштувань
        raw = resp.read(META_MAX_BYTES + 1)
    if len(raw) > META_MAX_BYTES:
        raise ValueError("meta too large")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("bad meta")
    return data


def read_meta(f: File) -> dict:
    from apps.storage.services import iter_plaintext

    kind = kind_of(f)
    if kind == "video":
        if not 0 < f.size <= thumbrender.MAX_VIDEO_INPUT_BYTES:
            return {}
        if settings.THUMBNAILER_URL:
            return _post_json("video", iter_plaintext(f), f.size)
        return thumbrender.extract_meta("video", b"".join(iter_plaintext(f)))
    if kind == "photo":
        if f.size > thumbrender.MAX_INPUT_BYTES:
            return {}
        data = b"".join(iter_plaintext(f))
        if settings.THUMBNAILER_URL:
            return _post_json("image", data, len(data))
        return thumbrender.extract_meta("image", data)
    return {}


# ─────────────────────────── Дата зйомки ───────────────────────────

NAME_RE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
    r"(?:[-_ T.]?([01]\d|2[0-3])[-_.:]?([0-5]\d)[-_.:]?([0-5]\d)?\d{0,3})?(?!\d)"
)
OFFSET_RE = re.compile(r"^([+-])(\d{2}):?(\d{2})$")


def _owner_tz(owner):
    try:
        return ZoneInfo(owner.preferences.timezone)
    except Exception:
        return ZoneInfo(settings.TIME_ZONE)


def _plausible(dt: datetime) -> bool:
    return datetime(1990, 1, 1, tzinfo=dt_timezone.utc) <= dt <= timezone.now() + timedelta(days=1)


def parse_taken(meta: dict, owner) -> datetime | None:
    raw = str(meta.get("taken_at") or "").strip()
    if not raw or raw.startswith("0000"):
        return None
    tz = _owner_tz(owner)
    dt = None
    try:
        if meta.get("kind") == "photo":
            dt = datetime.strptime(raw[:19], "%Y:%m:%d %H:%M:%S")
            m = OFFSET_RE.match(str(meta.get("offset") or ""))
            if m:
                delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
                dt = dt.replace(tzinfo=dt_timezone(delta if m.group(1) == "+" else -delta))
        else:
            value = raw.replace("Z", "+00:00")
            value = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value)  # Apple: +0200 → +02:00
            dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt if _plausible(dt) else None


def date_from_name(name: str, owner) -> datetime | None:
    m = NAME_RE.search(name or "")
    if not m:
        return None
    y, mo, d, hh, mm, ss = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(hh or 12), int(mm or 0), int(ss or 0), tzinfo=_owner_tz(owner))
    except ValueError:
        return None
    return dt if _plausible(dt) else None


# ─────────────────────────── Індекс ───────────────────────────


def ensure_row(f: File) -> PhotoMeta | None:
    kind = kind_of(f)
    if kind is None:
        return None
    try:
        row, _created = PhotoMeta.objects.get_or_create(
            file=f, defaults={"owner_id": f.owner_id, "kind": kind, "sort_at": f.created_at}
        )
    except IntegrityError:
        row = PhotoMeta.objects.get(file=f)
    return row


def add_missing(limit: int = 1000) -> int:
    """Медіафайли без запису в індексі → у стрічку (за датою завантаження) зі статусом pending."""
    missing = candidates().filter(photo__isnull=True).only("id", "owner_id", "mime_type", "created_at")[:limit]
    rows = [
        PhotoMeta(file=f, owner_id=f.owner_id, kind=kind_of(f) or "photo", sort_at=f.created_at, status=PhotoMeta.Status.PENDING)
        for f in missing
    ]
    PhotoMeta.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def index_file(f: File) -> PhotoMeta | None:
    """Читає метадані файлу й оновлює запис у стрічці. Помилка читання не страшна — лишається дата завантаження."""
    row = ensure_row(f)
    if row is None:
        return None
    try:
        meta = read_meta(f)
        failed = False
    except Exception as exc:  # пошкоджений файл, сервіс зайнятий, немає ffprobe
        logger.info("photo meta skipped for %s: %s", f.pk, type(exc).__name__)
        meta, failed = {}, True
    owner = f.owner
    taken = parse_taken(meta, owner) if meta else None
    source = "exif" if taken and row.kind == "photo" else "video" if taken else "upload"
    if taken is None:
        taken = date_from_name(f.name, owner)
        source = "name" if taken else "upload"
    lat, lon = meta.get("lat"), meta.get("lon")
    location = ""
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and thumbrender._valid_coords(lat, lon):
        parts = [f"{lat:.6f}", f"{lon:.6f}"]
        if isinstance(meta.get("alt"), (int, float)):
            parts.append(f"{meta['alt']:.1f}")
        location = field_crypto.encrypt_str(",".join(parts))

    def text(key):
        return str(meta.get(key) or "")[:100]

    def num(key, cast=int):
        try:
            value = cast(meta.get(key))
        except (TypeError, ValueError):
            return None
        return value if value and value > 0 else None

    attempts = row.attempts + 1 if failed else row.attempts
    PhotoMeta.objects.filter(pk=row.pk).update(
        status=PhotoMeta.Status.FAILED if failed and attempts >= MAX_ATTEMPTS else (PhotoMeta.Status.PENDING if failed else PhotoMeta.Status.OK),
        attempts=attempts,
        content_version=f.content_version,
        taken_at=taken,
        taken_source=source,
        sort_at=taken or f.created_at,
        camera_make=text("make"),
        camera_model=text("model"),
        lens=text("lens"),
        width=num("width"),
        height=num("height"),
        duration=num("duration", float),
        location=location,
        has_location=bool(location),
        extracted_at=timezone.now(),
    )
    row.refresh_from_db()
    return row


def pending(limit: int):
    """Що прочитати: нові (pending) і ті, чий файл отримав нову версію вмісту."""
    from django.db.models import F

    qs = PhotoMeta.objects.filter(file__status=File.Status.CLEAN, file__deleted_at__isnull=True).filter(
        Q(status=PhotoMeta.Status.PENDING) | (Q(status=PhotoMeta.Status.OK) & ~Q(content_version=F("file__content_version")))
    )
    return qs.select_related("file", "file__owner").order_by("-sort_at")[:limit]


def run(limit: int = 300) -> dict:
    added = add_missing(max(limit * 5, 1000))
    done = 0
    for row in pending(limit):
        index_file(row.file)
        done += 1
    return {"added": added, "indexed": done}


def location_of(row: PhotoMeta) -> dict | None:
    if not row.location:
        return None
    try:
        parts = [float(p) for p in field_crypto.decrypt_str(row.location).split(",")]
    except ValueError:
        return None
    out = {"lat": parts[0], "lon": parts[1]}
    if len(parts) > 2:
        out["alt"] = parts[2]
    return out
