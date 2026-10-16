import logging
import os
import unicodedata
import urllib.parse
from datetime import timedelta

import magic
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.db.models import F, Max, Sum
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from . import crypto
from .models import File, FileVersion, Folder, Share
from .objectstore import get_store

logger = logging.getLogger("blackcloud")

MAX_DEPTH = 64
FORBIDDEN_NAMES = {".", ".."}
# Типи, які дозволено показувати в браузері (inline). Решта — лише як вкладення.
SAFE_INLINE_MIME = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/avif",
    "application/pdf",
    "text/plain",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "video/mp4",
    "video/webm",
    "video/quicktime",  # .MOV з iPhone/камер (H.264/HEVC) — браузер відтворює, якщо підтримує кодек
}


# ─────────────────────────── Імена ───────────────────────────


def clean_name(name: str) -> str:
    """Нормалізує й перевіряє ім'я файлу/папки."""
    if not isinstance(name, str):
        raise ValidationError({"name": [_("Невірне ім'я.")]})
    name = unicodedata.normalize("NFC", name).strip()
    # Прибираємо керівні символи, у т.ч. bidi-override (підміна розширення)
    name = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cc", "Cf"))
    # Роздільники шляху відхиляємо явно, а не вирізаємо мовчки (інакше "a/b" стало б "ab")
    if "/" in name or "\\" in name:
        raise ValidationError({"name": [_("Ім'я не може містити / або \\.")]})
    if not name or name in FORBIDDEN_NAMES or len(name) > 255 or len(name.encode()) > 1024:
        raise ValidationError({"name": [_("Невірне ім'я (1–255 символів, без / і \\).")]})
    return name


def _name_taken(owner, folder, name: str, exclude_file=None, exclude_folder=None) -> bool:
    files = File.objects.filter(owner=owner, folder=folder, name=name, deleted_at__isnull=True).exclude(
        status=File.Status.FAILED
    )
    folders = Folder.objects.filter(owner=owner, parent=folder, name=name, deleted_at__isnull=True)
    if exclude_file is not None:
        files = files.exclude(pk=exclude_file.pk)
    if exclude_folder is not None:
        folders = folders.exclude(pk=exclude_folder.pk)
    return files.exists() or folders.exists()


def unique_name(owner, folder, name: str, **exclude) -> str:
    if not _name_taken(owner, folder, name, **exclude):
        return name
    stem, ext = os.path.splitext(name)
    for i in range(1, 1000):
        candidate = f"{stem} ({i}){ext}"
        if not _name_taken(owner, folder, candidate, **exclude):
            return candidate
    raise ValidationError({"name": [_("Забагато файлів з таким ім'ям.")]})


# ─────────────────────────── Дерево папок ───────────────────────────


def get_own_folder(user, folder_id) -> Folder | None:
    if folder_id in (None, "", "root"):
        return None
    folder = Folder.objects.filter(pk=folder_id, owner=user, deleted_at__isnull=True).first()
    if folder is None or is_in_trash(folder):
        raise NotFound(_("Папку не знайдено."))
    return folder


def ancestors(folder: Folder | None) -> list[Folder]:
    """Ланцюжок від кореня до папки (включно)."""
    chain = []
    current = folder
    while current is not None:
        chain.append(current)
        if len(chain) > MAX_DEPTH:
            break
        current = current.parent
    return list(reversed(chain))


def is_in_trash(folder: Folder | None) -> bool:
    return any(f.deleted_at is not None for f in ancestors(folder))


def descendant_folder_ids(folder: Folder) -> list:
    ids = [folder.pk]
    frontier = [folder.pk]
    while frontier:
        frontier = list(Folder.objects.filter(parent_id__in=frontier).values_list("pk", flat=True))
        ids.extend(frontier)
    return ids


def assert_not_descendant(folder: Folder, new_parent: Folder | None) -> None:
    if new_parent is None:
        return
    if folder.pk in {f.pk for f in ancestors(new_parent)}:
        raise ValidationError({"parent": [_("Не можна перемістити папку саму в себе.")]})
    if len(ancestors(new_parent)) >= MAX_DEPTH:
        raise ValidationError({"parent": [_("Надто велика вкладеність папок.")]})


# ─────────────────────────── Права доступу ───────────────────────────


def shared_folder_root(user, folder: Folder) -> Share | None:
    """Чи має user доступ до папки через спільний доступ до неї чи її предка."""
    chain = ancestors(folder)
    if any(f.deleted_at for f in chain):
        return None
    return Share.objects.filter(recipient=user, folder_id__in=[f.pk for f in chain]).first()


def can_read_file(user, f: File) -> bool:
    if f.deleted_at is not None:
        return f.owner_id == user.pk
    if f.owner_id == user.pk:
        return True
    if Share.objects.filter(recipient=user, file=f).exists():
        return True
    if f.folder is not None and shared_folder_root(user, f.folder) is not None:
        return True
    return False


def get_readable_file(user, file_id) -> File:
    f = File.objects.select_related("folder").filter(pk=file_id).first()
    if f is None or not can_read_file(user, f):
        raise NotFound(_("Файл не знайдено."))
    return f


def get_own_file(user, file_id, *, include_trashed=False) -> File:
    qs = File.objects.filter(pk=file_id, owner=user)
    if not include_trashed:
        qs = qs.filter(deleted_at__isnull=True)
    f = qs.first()
    if f is None:
        raise NotFound(_("Файл не знайдено."))
    return f


# ─────────────────────────── Завантаження ───────────────────────────


@transaction.atomic
def start_upload(user, *, name: str, size: int, folder: Folder | None) -> File:
    name = clean_name(name)
    if size < 0 or size > settings.MAX_FILE_SIZE:
        raise ValidationError({"size": [_("Максимальний розмір файлу — %(n)s ГБ.") % {"n": settings.MAX_FILE_SIZE // 1024**3}]})
    if not user.try_reserve_bytes(size):
        raise PermissionDenied(_("Недостатньо місця у сховищі."))
    name = unique_name(user, folder, name)
    f = File(
        owner=user,
        folder=folder,
        name=name,
        size=size,
        chunk_size=settings.UPLOAD_CHUNK_SIZE,
        upload_expires_at=timezone.now() + settings.UPLOAD_SESSION_TTL,
    )
    data_key = crypto.new_data_key()
    f.wrapped_key, f.key_version = crypto.wrap_key(data_key, f.id)
    f.save()
    return f


def write_chunk(f: File, index: int, data: bytes) -> File:
    if f.status != File.Status.UPLOADING:
        raise ValidationError({"detail": _("Завантаження вже завершено або скасовано.")})
    if f.upload_expires_at and f.upload_expires_at < timezone.now():
        raise ValidationError({"detail": _("Сесія завантаження прострочена.")})
    if index != f.chunks_received:
        raise ValidationError({"detail": _("Невірний порядок чанків."), "expected_index": f.chunks_received})
    if index >= f.chunks_total:
        raise ValidationError({"detail": _("Зайвий чанк.")})
    if len(data) != f.expected_chunk_len(index):
        raise ValidationError({"detail": _("Очікувалось %(expected)s байт, отримано %(got)s.") % {"expected": f.expected_chunk_len(index), "got": len(data)}})

    data_key = crypto.unwrap_key(f.wrapped_key, f.key_version, f.id)
    get_store().put(f.chunk_key(index), crypto.encrypt_chunk(data_key, f.id, index, data))

    updates = {"chunks_received": F("chunks_received") + 1}
    if index == 0:
        try:
            updates["mime_type"] = magic.from_buffer(data[:65536], mime=True)[:127]
        except Exception:
            logger.warning("mime detection failed", exc_info=True)
    # Оптимістичне блокування: паралельний дубль того ж чанка не зарахується двічі
    updated = File.objects.filter(pk=f.pk, chunks_received=index, status=File.Status.UPLOADING).update(**updates)
    if updated != 1:
        raise ValidationError({"detail": _("Конфлікт завантаження, повторіть.")})
    f.refresh_from_db()
    return f


def complete_upload(f: File) -> File:
    if f.status != File.Status.UPLOADING:
        raise ValidationError({"detail": _("Завантаження вже завершено.")})
    if f.chunks_received != f.chunks_total:
        raise ValidationError({"detail": _("Отримано не всі чанки."), "expected_index": f.chunks_received})
    updated = File.objects.filter(pk=f.pk, status=File.Status.UPLOADING).update(
        status=File.Status.SCANNING, upload_expires_at=None
    )
    if updated != 1:
        raise ValidationError({"detail": _("Конфлікт завантаження.")})
    f.refresh_from_db()
    _schedule_scan(str(f.pk))
    return f


def _schedule_scan(file_id: str) -> None:
    """Після коміту: невеликий файл — перевірка одразу, решта — черга worker-а."""
    from .tasks import scan_file, scan_small_file_now

    if settings.SCAN_INLINE_MAX_BYTES > 0:
        transaction.on_commit(lambda: scan_small_file_now(file_id))
    else:
        transaction.on_commit(lambda: scan_file.delay(file_id))


# ─────────────────────────── Читання ───────────────────────────


def read_chunk(f: File, data_key: bytes, index: int) -> bytes:
    return crypto.decrypt_chunk(data_key, f.id, index, get_store().get(f.chunk_key(index)))


def iter_plaintext(f: File):
    data_key = crypto.unwrap_key(f.wrapped_key, f.key_version, f.id)
    for index in range(f.chunks_total):
        yield read_chunk(f, data_key, index)


async def aiter_plaintext(f: File):
    """Асинхронний ітератор: під ASGI Django інакше буферизує весь файл у пам'ять."""
    data_key = crypto.unwrap_key(f.wrapped_key, f.key_version, f.id)
    for index in range(f.chunks_total):
        yield await sync_to_async(read_chunk, thread_sensitive=False)(f, data_key, index)


def parse_range(header: str, size: int):
    """Один діапазон bytes=a-b / a- / -n. None — віддати весь файл; "invalid" — 416."""
    if not header or not header.startswith("bytes=") or "," in header or size == 0:
        return None
    start_s, sep, end_s = header[6:].strip().partition("-")
    if not sep:
        return None
    try:
        if start_s == "":
            n = int(end_s)
            if n <= 0:
                return "invalid"
            return max(0, size - n), size - 1
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return "invalid"
    return start, min(end, size - 1)


async def aiter_range(f: File, start: int, end: int):
    data_key = crypto.unwrap_key(f.wrapped_key, f.key_version, f.id)
    cs = f.chunk_size
    for index in range(start // cs, end // cs + 1):
        chunk = await sync_to_async(read_chunk, thread_sensitive=False)(f, data_key, index)
        base = index * cs
        yield chunk[max(start - base, 0) : min(end - base + 1, len(chunk))]


def content_disposition(disposition: str, filename: str) -> str:
    ascii_name = filename.encode("ascii", "ignore").decode().replace('"', "").replace("\\", "") or "file"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(filename)}"


def file_response(
    f: File,
    *,
    inline: bool = False,
    frame_ancestor: str | None = None,
    range_header: str = "",
    scrub_location: bool = False,
) -> StreamingHttpResponse:
    """inline=True використовується лише на usercontent-піддомені (окремий origin).
    range_header (лише inline) — перемотування відео й аудіо; Safari без 206 відео не відтворює.
    scrub_location — публічні посилання: фото й відео без геолокації (geoscrub, розмір той самий)."""
    if not f.is_downloadable:
        raise PermissionDenied(_("Файл недоступний для завантаження (статус: %(status)s).") % {"status": f.get_status_display()})
    show_inline = inline and f.mime_type in SAFE_INLINE_MIME
    content_type = f.mime_type if show_inline else "application/octet-stream"
    if content_type == "text/plain":
        content_type = "text/plain; charset=utf-8"
    rng = parse_range(range_header, f.size) if show_inline else None
    if rng == "invalid":
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{f.size}"
        return response
    if scrub_location and f.mime_type.split("/")[0] in ("image", "video"):
        from . import geoscrub

        rng = None  # затирання — для всього файлу
        body = geoscrub.ascrub_chunks(aiter_plaintext(f))
    else:
        body = aiter_plaintext(f)
    if rng is None:
        response = StreamingHttpResponse(body, content_type=content_type)
        response["Content-Length"] = str(f.size)
    else:
        start, end = rng
        response = StreamingHttpResponse(aiter_range(f, start, end), content_type=content_type, status=206)
        response["Content-Range"] = f"bytes {start}-{end}/{f.size}"
        response["Content-Length"] = str(end - start + 1)
    if not show_inline:
        _attachment_headers(response, f.name)
        return response
    response["Accept-Ranges"] = "bytes"
    response["Content-Disposition"] = content_disposition("inline", f.name)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    if frame_ancestor:
        frame_rule = f"frame-ancestors {frame_ancestor}"
        if f.mime_type == "application/pdf":
            # Вбудований PDF-переглядач браузера не працює в CSP sandbox; ізоляцію
            # забезпечує окремий origin (usercontent.<домен>)
            # (default-src/object-src 'none' у Chrome блокують і сам переглядач PDF)
            response["Content-Security-Policy"] = frame_rule
        else:
            response["Content-Security-Policy"] = f"default-src 'none'; img-src 'self'; media-src 'self'; {frame_rule}; sandbox"
        # Вбудовується в <img>/<video>/<iframe> основного сайту
        response["Cross-Origin-Resource-Policy"] = "cross-origin"
    else:
        response["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; sandbox"
        response["Cross-Origin-Resource-Policy"] = "same-origin"
        response["X-Frame-Options"] = "DENY"
    return response


# ─────────────────────────── Версії вмісту ───────────────────────────
#
# Поточний вміст завжди описує сам File (ключ, розмір, статус антивірусу), тому
# завантаження, перегляд, мініатюри й ONLYOFFICE працюють як раніше. Попередні версії —
# рядки FileVersion з власним ключем даних; їхні чанки лежать за File.chunk_key(i, number).
# Кожна версія займає місце в квоті власника. Нова версія завжди проходить антивірус.


def max_versions() -> int:
    """Скільки версій (разом із поточною) зберігати на файл; 1 — без історії."""
    return max(1, int(getattr(settings, "FILE_MAX_VERSIONS", 20)))


def _archived(f: File):
    return FileVersion.objects.filter(file=f).exclude(status=File.Status.UPLOADING)


def _next_number(f: File) -> int:
    """Наступний номер версії (виклик лише під select_for_update рядка File)."""
    top = FileVersion.objects.filter(file=f).aggregate(m=Max("number"))["m"]
    # Лічильник не дає повторно видати номер видаленої версії (її об'єкти стираються після коміту)
    number = max(f.content_version, f.version_counter, -1 if top is None else top) + 1
    File.objects.filter(pk=f.pk).update(version_counter=number)
    f.version_counter = number
    return number


def _version_keys(f: File, number: int, chunks: int) -> list[str]:
    return [f.chunk_key(i, number) for i in range(chunks)]


def _drop_versions(f: File, versions) -> list[str]:
    """Видаляє рядки версій і звільняє їхню квоту (у поточній транзакції).

    Повертає ключі об'єктів, які треба стерти зі сховища ПІСЛЯ коміту."""
    keys, total = [], 0
    for v in versions:
        if FileVersion.objects.filter(pk=v.pk).delete()[0]:
            total += v.size
            keys += _version_keys(f, v.number, v.chunks_total)
    f.owner.release_bytes(total)
    return keys


def _delete_objects_on_commit(keys: list[str]) -> None:
    if not keys:
        return

    def _run():
        try:
            get_store().delete(keys)
        except Exception:
            logger.warning("failed to delete %s stale objects", len(keys), exc_info=True)

    transaction.on_commit(_run)


def _reserve_with_pruning(f: File, size: int, keep_ids=()) -> list[str]:
    """Резервує місце; якщо квоти бракує — видаляє найстаріші архівні версії ЦЬОГО файлу.

    Якщо місця не вистачає навіть без історії — PermissionDenied (транзакція відкотиться,
    тож і видалені версії повернуться)."""
    keys: list[str] = []
    while not f.owner.try_reserve_bytes(size):
        victim = _archived(f).exclude(pk__in=list(keep_ids)).order_by("number").first()
        if victim is None:
            raise PermissionDenied(_("Недостатньо місця у сховищі для збереження."))
        keys += _drop_versions(f, [victim])
    return keys


def _prune_count(f: File, keep_ids=()) -> list[str]:
    """Залишає не більше max_versions() - 1 архівних версій (найновіші)."""
    limit = max_versions() - 1
    archived = list(_archived(f).order_by("-number"))
    excess = [v for v in archived[limit:] if v.pk not in set(keep_ids)]
    return _drop_versions(f, excess)


def _swap_in(f: File, *, number, size, wrapped, key_version, chunk_size, chunks, mime, author, current_released=False):
    """Робить новий вміст поточним: старий поточний стає архівною версією (або видаляється,
    якщо історію вимкнено). Виклик лише під select_for_update. Повертає (ключі до видалення, архівна версія)."""
    drop: list[str] = []
    archived = None
    if f.has_thumbnail:
        drop.append(f.thumb_key())
    if f.has_preview:
        drop.append(f.preview_key())
    if max_versions() > 1:
        archived = FileVersion.objects.create(
            file=f,
            number=f.content_version,
            size=f.size,
            mime_type=f.mime_type,
            sha256=f.sha256,
            status=f.status,
            scan_detail=f.scan_detail,
            wrapped_key=f.wrapped_key,
            key_version=f.key_version,
            chunk_size=f.chunk_size,
            chunks_received=f.chunks_total,
            author_id=f.modified_by_id or f.owner_id,
            created_at=f.content_updated_at or f.created_at,
        )
    else:
        if not current_released:
            f.owner.release_bytes(f.size)
        drop += _version_keys(f, f.content_version, f.chunks_total)
    now = timezone.now()
    File.objects.filter(pk=f.pk).update(
        size=size,
        wrapped_key=wrapped,
        key_version=key_version,
        chunk_size=chunk_size,
        chunks_received=chunks,
        content_version=number,
        mime_type=mime,
        sha256="",
        status=File.Status.SCANNING,
        scan_detail="",
        has_thumbnail=False,
        has_preview=False,
        modified_by=author,
        content_updated_at=now,
        updated_at=now,
    )
    drop += _prune_count(f)
    return drop, archived


def _after_swap(f: File, drop: list[str], archived) -> None:
    from .tasks import scan_version

    _delete_objects_on_commit(drop)
    _schedule_scan(str(f.pk))
    if archived is not None and archived.status == File.Status.SCANNING:
        # Попередня версія ще не встигла пройти антивірус — перевіряємо її окремо
        version_id = str(archived.pk)
        transaction.on_commit(lambda: scan_version.delay(version_id))


def _write_version_chunks(f: File, number: int, data_key: bytes, chunks, size: int, chunk_size: int):
    """Перерізає довільні шматки відкритого тексту на чанки chunk_size і шифрує їх.

    Повертає (кількість чанків, перші байти для визначення MIME)."""
    store = get_store()
    written: list[str] = []
    buf = bytearray()
    head = b""
    total = 0
    index = 0

    def put(data: bytes):
        nonlocal index
        key = f.chunk_key(index, number)
        store.put(key, crypto.encrypt_chunk(data_key, f.id, index, data))
        written.append(key)
        index += 1

    try:
        for piece in chunks:
            total += len(piece)
            if total > size:
                raise ValidationError({"detail": _("Розмір даних не збігається з оголошеним.")})
            if len(head) < 65536:
                head += bytes(piece[: 65536 - len(head)])
            buf += piece
            while len(buf) >= chunk_size:
                put(bytes(buf[:chunk_size]))
                del buf[:chunk_size]
        if buf:
            put(bytes(buf))
        if total != size:
            raise ValidationError({"detail": _("Розмір даних не збігається з оголошеним.")})
    except BaseException:
        try:
            store.delete(written)
        except Exception:
            logger.warning("failed to clean partial version of %s", f.pk, exc_info=True)
        raise
    return index, head


def store_new_version(f: File, chunks, *, size: int, author=None, keep_ids=()) -> File:
    """Записує новий вміст файлу як нову поточну версію (публічний помічник, напр. для WebDAV).

    chunks — ітерабельне з байтами відкритого тексту довільної довжини (сумарно рівно size).
    Попередній вміст стає архівною версією, квота резервується атомарно (за потреби —
    ціною найстаріших версій цього файлу), новий вміст отримує новий ключ і йде на антивірус."""
    if size < 0 or size > settings.MAX_FILE_SIZE:
        raise ValidationError({"detail": _("Файл завеликий.")})
    with transaction.atomic():
        f = File.objects.select_for_update().select_related("owner").get(pk=f.pk)
        if f.deleted_at is not None or f.status == File.Status.UPLOADING:
            raise ValidationError({"detail": _("Файл недоступний для зміни.")})
        drop: list[str] = []
        released = False
        if max_versions() > 1:
            # Звільняємо місце під ліміт кількості ще до резервування квоти
            limit = max_versions() - 2
            archived = [v for v in _archived(f).order_by("-number") if v.pk not in set(keep_ids)]
            drop += _drop_versions(f, archived[max(limit, 0) :])
        else:
            f.owner.release_bytes(f.size)
            released = True
        drop += _reserve_with_pruning(f, size, keep_ids)
        number = _next_number(f)
        chunk_size = settings.UPLOAD_CHUNK_SIZE
        data_key = crypto.new_data_key()
        wrapped, key_version = crypto.wrap_key(data_key, f.id)
        count, head = _write_version_chunks(f, number, data_key, chunks, size, chunk_size)
        try:
            mime = magic.from_buffer(head, mime=True)[:127] if head else f.mime_type
        except Exception:
            mime = f.mime_type
        more, archived_v = _swap_in(
            f,
            number=number,
            size=size,
            wrapped=wrapped,
            key_version=key_version,
            chunk_size=chunk_size,
            chunks=count,
            mime=mime,
            author=author,
            current_released=released,
        )
        _after_swap(f, drop + more, archived_v)
    f.refresh_from_db()
    return f


def replace_content(f: File, data: bytes, *, author=None) -> File:
    """Нова версія з онлайн-редактора (сигнатура збережена для сумісності)."""
    if len(data) > settings.MAX_FILE_SIZE:
        raise ValidationError({"detail": _("Файл завеликий.")})
    return store_new_version(f, [data], size=len(data), author=author or f.owner)


def iter_version_plaintext(v: FileVersion):
    """Розшифрований вміст архівної версії (чанк за чанком)."""
    f = v.file
    data_key = crypto.unwrap_key(v.wrapped_key, v.key_version, f.id)
    store = get_store()
    for index in range(v.chunks_total):
        yield crypto.decrypt_chunk(data_key, f.id, index, store.get(f.chunk_key(index, v.number)))


async def aiter_version_plaintext(v: FileVersion):
    data_key = crypto.unwrap_key(v.wrapped_key, v.key_version, v.file.id)
    store = get_store()

    def read(index):
        return crypto.decrypt_chunk(data_key, v.file.id, index, store.get(v.file.chunk_key(index, v.number)))

    for index in range(v.chunks_total):
        yield await sync_to_async(read, thread_sensitive=False)(index)


def get_own_version(user, file_id, version_id) -> tuple[File, FileVersion | None]:
    """Файл власника і його версія; version_id == id файлу означає поточну версію (None)."""
    f = get_own_file(user, file_id)
    if str(version_id) == str(f.pk):
        return f, None
    v = _archived(f).filter(pk=version_id).first()
    if v is None:
        raise NotFound(_("Версію не знайдено."))
    v.file = f
    return f, v


def list_versions(f: File) -> list[dict]:
    owner_name = f.owner.username
    names = {}

    def author_name(uid):
        if uid is None:
            return ""
        if uid not in names:
            from django.contrib.auth import get_user_model

            names[uid] = get_user_model().objects.filter(pk=uid).values_list("username", flat=True).first() or ""
        return names[uid]

    names[f.owner_id] = owner_name
    items = [
        {
            "id": str(f.pk),
            "number": f.content_version,
            "created_at": f.content_updated_at or f.created_at,
            "size": f.size,
            "author": author_name(f.modified_by_id or f.owner_id),
            "status": f.status,
            "status_display": f.get_status_display(),
            "downloadable": f.is_downloadable,
            "is_current": True,
        }
    ]
    for v in _archived(f).order_by("-number"):
        items.append(
            {
                "id": str(v.pk),
                "number": v.number,
                "created_at": v.created_at,
                "size": v.size,
                "author": author_name(v.author_id),
                "status": v.status,
                "status_display": v.get_status_display(),
                "downloadable": v.is_downloadable,
                "is_current": False,
            }
        )
    return items


def restore_version(f: File, v: FileVersion, user) -> File:
    """Відновлення копіюванням (copy-on-write): вміст версії перешифровується новим ключем
    у НОВУ поточну версію, яка знову проходить антивірус. Історія лишається незмінною."""
    if not v.is_downloadable:
        raise ValidationError({"detail": _("Відновити можна лише версію, перевірену антивірусом.")})
    return store_new_version(f, iter_version_plaintext(v), size=v.size, author=user, keep_ids=[v.pk])


def delete_version(f: File, v: FileVersion) -> None:
    with transaction.atomic():
        f = File.objects.select_for_update().select_related("owner").get(pk=f.pk)
        if not _archived(f).filter(pk=v.pk).exists():
            raise NotFound(_("Версію не знайдено."))
        _delete_objects_on_commit(_drop_versions(f, [v]))


def version_response(f: File, v: FileVersion) -> StreamingHttpResponse:
    """Завантаження архівної версії — ті самі правила, що й для файлу (attachment, sandbox)."""
    if not v.is_downloadable:
        raise PermissionDenied(_("Версія недоступна для завантаження (статус: %(status)s).") % {"status": v.get_status_display()})
    stem, ext = os.path.splitext(f.name)
    response = StreamingHttpResponse(aiter_version_plaintext(v), content_type="application/octet-stream")
    response["Content-Length"] = str(v.size)
    _attachment_headers(response, f"{stem} (v{v.number}){ext}")
    return response


def _attachment_headers(response, filename: str) -> None:
    response["Content-Disposition"] = content_disposition("attachment", filename)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; sandbox"
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    response["X-Frame-Options"] = "DENY"


# ─── Завантаження нової версії чанками (upload з replace_file_id) ───


def start_version_upload(user, target: File, size: int) -> FileVersion:
    if size < 0 or size > settings.MAX_FILE_SIZE:
        raise ValidationError({"size": [_("Максимальний розмір файлу — %(n)s ГБ.") % {"n": settings.MAX_FILE_SIZE // 1024**3}]})
    with transaction.atomic():
        f = File.objects.select_for_update().select_related("owner").filter(pk=target.pk, owner=user).first()
        if f is None or f.deleted_at is not None:
            raise NotFound(_("Файл не знайдено."))
        if f.status == File.Status.UPLOADING:
            raise ValidationError({"detail": _("Файл недоступний для зміни.")})
        try:
            drop = _reserve_with_pruning(f, size)
        except PermissionDenied:
            raise PermissionDenied(_("Недостатньо місця у сховищі."))
        v = FileVersion(
            file=f,
            number=_next_number(f),
            size=size,
            chunk_size=settings.UPLOAD_CHUNK_SIZE,
            status=File.Status.UPLOADING,
            upload_expires_at=timezone.now() + settings.UPLOAD_SESSION_TTL,
            author=user,
        )
        v.wrapped_key, v.key_version = crypto.wrap_key(crypto.new_data_key(), f.id)
        v.save()
        _delete_objects_on_commit(drop)
    return v


def get_own_upload(user, pk) -> File | FileVersion:
    """Сесія завантаження: новий файл (File) або нова версія наявного (FileVersion)."""
    f = File.objects.filter(pk=pk, owner=user, deleted_at__isnull=True).first()
    if f is not None:
        return f
    v = (
        FileVersion.objects.select_related("file", "file__owner")
        .filter(pk=pk, file__owner=user, status=File.Status.UPLOADING)
        .first()
    )
    if v is None:
        raise NotFound(_("Файл не знайдено."))
    return v


def write_version_chunk(v: FileVersion, index: int, data: bytes) -> FileVersion:
    if v.status != File.Status.UPLOADING:
        raise ValidationError({"detail": _("Завантаження вже завершено або скасовано.")})
    if v.upload_expires_at and v.upload_expires_at < timezone.now():
        raise ValidationError({"detail": _("Сесія завантаження прострочена.")})
    if index != v.chunks_received:
        raise ValidationError({"detail": _("Невірний порядок чанків."), "expected_index": v.chunks_received})
    if index >= v.chunks_total:
        raise ValidationError({"detail": _("Зайвий чанк.")})
    if len(data) != v.expected_chunk_len(index):
        raise ValidationError({"detail": _("Очікувалось %(expected)s байт, отримано %(got)s.") % {"expected": v.expected_chunk_len(index), "got": len(data)}})
    f = v.file
    data_key = crypto.unwrap_key(v.wrapped_key, v.key_version, f.id)
    get_store().put(f.chunk_key(index, v.number), crypto.encrypt_chunk(data_key, f.id, index, data))
    updates = {"chunks_received": F("chunks_received") + 1}
    if index == 0:
        try:
            updates["mime_type"] = magic.from_buffer(data[:65536], mime=True)[:127]
        except Exception:
            logger.warning("mime detection failed", exc_info=True)
    updated = FileVersion.objects.filter(pk=v.pk, chunks_received=index, status=File.Status.UPLOADING).update(**updates)
    if updated != 1:
        raise ValidationError({"detail": _("Конфлікт завантаження, повторіть.")})
    v.refresh_from_db()
    return v


def complete_version_upload(v: FileVersion) -> File:
    if v.status != File.Status.UPLOADING:
        raise ValidationError({"detail": _("Завантаження вже завершено.")})
    if v.chunks_received != v.chunks_total:
        raise ValidationError({"detail": _("Отримано не всі чанки."), "expected_index": v.chunks_received})
    with transaction.atomic():
        f = File.objects.select_for_update().select_related("owner").get(pk=v.file_id)
        v = FileVersion.objects.select_for_update().filter(pk=v.pk, status=File.Status.UPLOADING).first()
        if v is None:
            raise ValidationError({"detail": _("Конфлікт завантаження.")})
        if f.deleted_at is not None or f.status == File.Status.UPLOADING:
            raise ValidationError({"detail": _("Файл недоступний для зміни.")})
        params = dict(
            number=v.number,
            size=v.size,
            wrapped=v.wrapped_key,
            key_version=v.key_version,
            chunk_size=v.chunk_size,
            chunks=v.chunks_received,
            mime=v.mime_type,
            author=v.author,
        )
        # Квота вже зарезервована на старті — переходить від сесії до поточної версії
        v.delete()
        drop, archived = _swap_in(f, **params)
        _after_swap(f, drop, archived)
    f.refresh_from_db()
    return f


def abort_version_upload(v: FileVersion) -> None:
    with transaction.atomic():
        f = File.objects.select_for_update().select_related("owner").get(pk=v.file_id)
        v = FileVersion.objects.filter(pk=v.pk, status=File.Status.UPLOADING).first()
        if v is None:
            raise ValidationError({"detail": _("Завантаження вже завершено.")})
        _delete_objects_on_commit(_drop_versions(f, [v]))


def prune_versions(f: File, *, older_than=None) -> int:
    """Видаляє зайві (понад FILE_MAX_VERSIONS) і, за потреби, застарілі архівні версії."""
    with transaction.atomic():
        f = File.objects.select_for_update().select_related("owner").get(pk=f.pk)
        keys = _prune_count(f)
        if older_than is not None:
            keys += _drop_versions(f, list(_archived(f).filter(archived_at__lt=older_than)))
        _delete_objects_on_commit(keys)
    return len(keys)


def read_thumbnail(f: File) -> bytes:
    data_key = crypto.unwrap_key(f.wrapped_key, f.key_version, f.id)
    return crypto.decrypt_chunk(data_key, f.id, crypto.THUMB_INDEX, get_store().get(f.thumb_key()))


def read_preview_image(f: File) -> bytes:
    data_key = crypto.unwrap_key(f.wrapped_key, f.key_version, f.id)
    return crypto.decrypt_chunk(data_key, f.id, crypto.PREVIEW_INDEX, get_store().get(f.preview_key()))


# ─────────────────────────── Видалення ───────────────────────────


def purge_file(f: File) -> None:
    """Остаточне видалення: об'єкти зі сховища + звільнення квоти."""
    try:
        get_store().delete_prefix(f.object_prefix)
    except Exception:
        logger.exception("failed to delete objects for %s", f.pk)
        raise
    owner = f.owner
    # Квота: поточний вміст + усі версії (архівні й незавершені завантаження)
    size = f.size + (FileVersion.objects.filter(file=f).aggregate(s=Sum("size"))["s"] or 0)
    f.delete()
    owner.release_bytes(size)


def purge_folder(folder: Folder) -> int:
    ids = descendant_folder_ids(folder)
    count = 0
    for f in File.objects.filter(folder_id__in=ids).select_related("owner").iterator():
        purge_file(f)
        count += 1
    Folder.objects.filter(pk__in=ids).delete()
    return count


def trash_cutoff():
    return timezone.now() - settings.TRASH_RETENTION


def public_link_expiry(days: int):
    days = max(1, min(int(days), settings.PUBLIC_LINK_MAX_DAYS))
    return timezone.now() + timedelta(days=days)
