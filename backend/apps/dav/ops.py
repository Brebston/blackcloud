"""Операції WebDAV поверх сервісів apps.storage.

Нові файли проходять той самий шлях, що й завантаження з вебінтерфейсу
(start_upload → write_chunk → complete_upload): шифрування, атомарна квота,
ліміт розміру, ClamAV, мініатюри. Перезапис наявного файлу — нова версія через
services.store_new_version (історія версій, антивірус). Видалення — у кошик.
"""

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.exceptions import ValidationError

from apps.core.audit import audit
from apps.storage import services
from apps.storage.models import File, Folder

from . import paths

logger = logging.getLogger("blackcloud")

READ_PIECE = 1024 * 1024
COPY_MAX_FILES = 1000


class Conflict(Exception):
    """Ресурс змінився паралельно (інший клієнт створив файл з тим самим ім'ям)."""


class BodyMismatch(Exception):
    """Тіло коротше/довше, ніж оголошено."""


class CopyTooLarge(Exception):
    pass


class IterStream:
    """Файлоподібна обгортка над ітератором шматків байтів (read(n))."""

    def __init__(self, iterable):
        self._it = iter(iterable)
        self._buf = bytearray()

    def read(self, n: int = -1) -> bytes:
        while n < 0 or len(self._buf) < n:
            try:
                self._buf += next(self._it)
            except StopIteration:
                break
        if n < 0:
            n = len(self._buf)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


def read_exact(stream, n: int) -> bytes:
    parts = []
    remaining = n
    while remaining > 0:
        piece = stream.read(min(remaining, READ_PIECE))
        if not piece:
            break
        parts.append(piece)
        remaining -= len(piece)
    return b"".join(parts)


def iter_stream(stream, size: int):
    """Рівно size байтів шматками; менше — BodyMismatch."""
    remaining = size
    while remaining > 0:
        piece = stream.read(min(remaining, READ_PIECE))
        if not piece:
            raise BodyMismatch()
        remaining -= len(piece)
        yield piece


# ─────────────────────────── Файли ───────────────────────────


def create_file(request, user, folder: Folder | None, name: str, stream, size: int) -> File:
    """Новий файл з потоку (ім'я має бути вільним)."""
    f = services.start_upload(user, name=name, size=size, folder=folder)
    try:
        if f.name != services.clean_name(name):
            raise Conflict()
        for index in range(f.chunks_total):
            expected = f.expected_chunk_len(index)
            data = read_exact(stream, expected)
            if len(data) != expected:
                raise BodyMismatch()
            f = services.write_chunk(f, index, data)
        with transaction.atomic():
            f = services.complete_upload(f)
    except BaseException:
        try:
            services.purge_file(File.objects.select_related("owner").get(pk=f.pk))
        except Exception:
            logger.warning("failed to clean up dav upload %s", f.pk, exc_info=True)
        raise
    audit(request, "file.uploaded", user=user, target=str(f.pk), size=f.size, via="dav")
    return f


def overwrite_file(request, user, f: File, stream, size: int) -> File:
    """Новий вміст наявного файлу = нова версія (стара лишається в історії версій)."""
    f = services.store_new_version(f, iter_stream(stream, size), size=size, author=user)
    audit(request, "file.updated", user=user, target=str(f.pk), size=f.size, via="dav")
    return f


def copy_file(request, user, src: File, folder: Folder | None, name: str) -> File:
    """Копія = розшифрування й повторне шифрування (ключ і AAD прив'язані до id файлу),
    нова копія знову проходить антивірус. Копіювати можна лише перевірені файли."""
    stream = IterStream(services.iter_plaintext(src))
    f = create_file(request, user, folder, name, stream, src.size)
    return f


def trash(request, user, node: "paths.Node") -> None:
    now = timezone.now()
    if node.file is not None:
        File.objects.filter(pk=node.file.pk).update(deleted_at=now)
        audit(request, "file.trashed", user=user, target=str(node.file.pk), via="dav")
    elif node.folder is not None:
        Folder.objects.filter(pk=node.folder.pk).update(deleted_at=now)
        audit(request, "folder.trashed", user=user, target=str(node.folder.pk), via="dav")


def move(request, user, node: "paths.Node", parent: Folder | None, name: str) -> None:
    name = services.clean_name(name)
    with transaction.atomic():
        if services._name_taken(
            user,
            parent,
            name,
            **({"exclude_file": node.file} if node.file is not None else {"exclude_folder": node.folder}),
        ):
            raise Conflict()
        if node.file is not None:
            f = node.file
            f.folder = parent
            f.name = name
            f.save(update_fields=["folder", "name", "updated_at"])
            audit(request, "file.updated", user=user, target=str(f.pk), via="dav")
        else:
            folder = node.folder
            services.assert_not_descendant(folder, parent)
            folder.parent = parent
            folder.name = name
            folder.save(update_fields=["parent", "name", "updated_at"])
            audit(request, "folder.updated", user=user, target=str(folder.pk), via="dav")


def make_folder(request, user, parent: Folder | None, name: str) -> Folder:
    name = services.clean_name(name)
    if parent is not None and len(services.ancestors(parent)) >= services.MAX_DEPTH:
        raise ValidationError({"parent": [_("Надто велика вкладеність папок.")]})
    with transaction.atomic():
        if services._name_taken(user, parent, name):
            raise Conflict()
        folder = Folder.objects.create(owner=user, parent=parent, name=name)
    audit(request, "folder.created", user=user, target=str(folder.pk), via="dav")
    return folder


def _collect(user, folder: Folder, limit_files: int, limit_bytes: int):
    """Обхід піддерева для COPY з лімітами (кількість файлів і сумарний розмір)."""
    files, folders, total = 0, 0, 0
    frontier = [folder]
    while frontier:
        nxt = []
        for fo in frontier:
            for f in paths.visible_files(File.objects.filter(owner=user, folder=fo)):
                if not f.is_downloadable:
                    raise PermissionError(f.name)
                files += 1
                total += f.size
            nxt += list(Folder.objects.filter(owner=user, parent=fo, deleted_at__isnull=True))
            folders += 1
        if files > limit_files or total > limit_bytes or folders > limit_files:
            raise CopyTooLarge()
        frontier = nxt
    return files, total


def copy_folder(request, user, src: Folder, parent: Folder | None, name: str) -> Folder:
    limit_bytes = getattr(settings, "DAV_COPY_MAX_BYTES", 2 * 1024**3)
    _collect(user, src, COPY_MAX_FILES, limit_bytes)
    if parent is not None:
        services.assert_not_descendant(src, parent)
    root = make_folder(request, user, parent, name)
    stack = [(src, root)]
    while stack:
        s, d = stack.pop()
        for f in paths.visible_files(File.objects.filter(owner=user, folder=s)).select_related("owner"):
            copy_file(request, user, f, d, f.name)
        for child in Folder.objects.filter(owner=user, parent=s, deleted_at__isnull=True):
            new = Folder.objects.create(owner=user, parent=d, name=child.name)
            stack.append((child, new))
    return root
