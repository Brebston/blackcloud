import logging
import os
import unicodedata
import urllib.parse
from datetime import timedelta

import magic
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from . import crypto
from .models import File, Folder, Share
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
}


# ─────────────────────────── Імена ───────────────────────────


def clean_name(name: str) -> str:
    """Нормалізує й перевіряє ім'я файлу/папки."""
    if not isinstance(name, str):
        raise ValidationError({"name": ["Невірне ім'я."]})
    name = unicodedata.normalize("NFC", name).strip()
    # Прибираємо керівні символи, у т.ч. bidi-override (підміна розширення)
    name = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cc", "Cf"))
    # Роздільники шляху відхиляємо явно, а не вирізаємо мовчки (інакше "a/b" стало б "ab")
    if "/" in name or "\\" in name:
        raise ValidationError({"name": ["Ім'я не може містити / або \\."]})
    if not name or name in FORBIDDEN_NAMES or len(name) > 255 or len(name.encode()) > 1024:
        raise ValidationError({"name": ["Невірне ім'я (1–255 символів, без / і \\)."]})
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
    raise ValidationError({"name": ["Забагато файлів з таким ім'ям."]})


# ─────────────────────────── Дерево папок ───────────────────────────


def get_own_folder(user, folder_id) -> Folder | None:
    if folder_id in (None, "", "root"):
        return None
    folder = Folder.objects.filter(pk=folder_id, owner=user, deleted_at__isnull=True).first()
    if folder is None or is_in_trash(folder):
        raise NotFound("Папку не знайдено.")
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
        raise ValidationError({"parent": ["Не можна перемістити папку саму в себе."]})
    if len(ancestors(new_parent)) >= MAX_DEPTH:
        raise ValidationError({"parent": ["Надто велика вкладеність папок."]})


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
        raise NotFound("Файл не знайдено.")
    return f


def get_own_file(user, file_id, *, include_trashed=False) -> File:
    qs = File.objects.filter(pk=file_id, owner=user)
    if not include_trashed:
        qs = qs.filter(deleted_at__isnull=True)
    f = qs.first()
    if f is None:
        raise NotFound("Файл не знайдено.")
    return f


# ─────────────────────────── Завантаження ───────────────────────────


@transaction.atomic
def start_upload(user, *, name: str, size: int, folder: Folder | None) -> File:
    name = clean_name(name)
    if size < 0 or size > settings.MAX_FILE_SIZE:
        raise ValidationError({"size": [f"Максимальний розмір файлу — {settings.MAX_FILE_SIZE // 1024**3} ГБ."]})
    if not user.try_reserve_bytes(size):
        raise PermissionDenied("Недостатньо місця у сховищі.")
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
        raise ValidationError({"detail": "Завантаження вже завершено або скасовано."})
    if f.upload_expires_at and f.upload_expires_at < timezone.now():
        raise ValidationError({"detail": "Сесія завантаження прострочена."})
    if index != f.chunks_received:
        raise ValidationError({"detail": "Невірний порядок чанків.", "expected_index": f.chunks_received})
    if index >= f.chunks_total:
        raise ValidationError({"detail": "Зайвий чанк."})
    if len(data) != f.expected_chunk_len(index):
        raise ValidationError({"detail": f"Очікувалось {f.expected_chunk_len(index)} байт, отримано {len(data)}."})

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
        raise ValidationError({"detail": "Конфлікт завантаження, повторіть."})
    f.refresh_from_db()
    return f


def complete_upload(f: File) -> File:
    if f.status != File.Status.UPLOADING:
        raise ValidationError({"detail": "Завантаження вже завершено."})
    if f.chunks_received != f.chunks_total:
        raise ValidationError({"detail": "Отримано не всі чанки.", "expected_index": f.chunks_received})
    updated = File.objects.filter(pk=f.pk, status=File.Status.UPLOADING).update(
        status=File.Status.SCANNING, upload_expires_at=None
    )
    if updated != 1:
        raise ValidationError({"detail": "Конфлікт завантаження."})
    f.refresh_from_db()
    from .tasks import scan_file

    transaction.on_commit(lambda: scan_file.delay(str(f.pk)))
    return f


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


def content_disposition(disposition: str, filename: str) -> str:
    ascii_name = filename.encode("ascii", "ignore").decode().replace('"', "").replace("\\", "") or "file"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(filename)}"


def file_response(f: File, *, inline: bool = False, frame_ancestor: str | None = None) -> StreamingHttpResponse:
    """inline=True використовується лише на usercontent-піддомені (окремий origin)."""
    if not f.is_downloadable:
        raise PermissionDenied("Файл недоступний для завантаження (статус: %s)." % f.get_status_display())
    show_inline = inline and f.mime_type in SAFE_INLINE_MIME
    content_type = f.mime_type if show_inline else "application/octet-stream"
    if content_type == "text/plain":
        content_type = "text/plain; charset=utf-8"
    response = StreamingHttpResponse(aiter_plaintext(f), content_type=content_type)
    response["Content-Length"] = str(f.size)
    response["Content-Disposition"] = content_disposition("inline" if show_inline else "attachment", f.name)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    if show_inline and frame_ancestor:
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


# ─────────────────────────── Заміна вмісту (онлайн-редактор) ───────────────────────────


def replace_content(f: File, data: bytes) -> File:
    """Записує нову версію файлу (новий ключ даних), стару видаляє після коміту.

    Квота коригується атомарно на різницю розмірів.
    """
    from .tasks import scan_file

    if len(data) > settings.MAX_FILE_SIZE:
        raise ValidationError({"detail": "Файл завеликий."})
    with transaction.atomic():
        f = File.objects.select_for_update().select_related("owner").get(pk=f.pk)
        delta = len(data) - f.size
        if delta > 0 and not f.owner.try_reserve_bytes(delta):
            raise PermissionDenied("Недостатньо місця у сховищі для збереження.")
        if delta < 0:
            f.owner.release_bytes(-delta)

        old_version, old_chunks, old_thumb = f.content_version, f.chunks_total, f.has_thumbnail
        new_version = old_version + 1
        chunk_size = settings.UPLOAD_CHUNK_SIZE
        data_key = crypto.new_data_key()
        wrapped, key_version = crypto.wrap_key(data_key, f.id)
        store = get_store()
        count = 0
        for count, offset in enumerate(range(0, len(data), chunk_size), start=1):
            index = count - 1
            store.put(
                f.chunk_key(index, new_version),
                crypto.encrypt_chunk(data_key, f.id, index, data[offset : offset + chunk_size]),
            )
        try:
            mime = magic.from_buffer(data[:65536], mime=True)[:127] if data else f.mime_type
        except Exception:
            mime = f.mime_type
        File.objects.filter(pk=f.pk).update(
            size=len(data),
            wrapped_key=wrapped,
            key_version=key_version,
            chunk_size=chunk_size,
            chunks_received=count,
            content_version=new_version,
            mime_type=mime,
            sha256="",
            status=File.Status.SCANNING,
            scan_detail="",
            has_thumbnail=False,
            updated_at=timezone.now(),
        )
        old_keys = [f.chunk_key(i, old_version) for i in range(old_chunks)]
        if old_thumb:
            old_keys.append(f.thumb_key(old_version))

        def _after_commit():
            try:
                store.delete(old_keys)
            except Exception:
                logger.warning("failed to delete old version of %s", f.pk, exc_info=True)
            scan_file.delay(str(f.pk))

        transaction.on_commit(_after_commit)
    f.refresh_from_db()
    return f


def read_thumbnail(f: File) -> bytes:
    data_key = crypto.unwrap_key(f.wrapped_key, f.key_version, f.id)
    return crypto.decrypt_chunk(data_key, f.id, crypto.THUMB_INDEX, get_store().get(f.thumb_key()))


# ─────────────────────────── Видалення ───────────────────────────


def purge_file(f: File) -> None:
    """Остаточне видалення: об'єкти зі сховища + звільнення квоти."""
    try:
        get_store().delete_prefix(f.object_prefix)
    except Exception:
        logger.exception("failed to delete objects for %s", f.pk)
        raise
    owner = f.owner
    size = f.size
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
