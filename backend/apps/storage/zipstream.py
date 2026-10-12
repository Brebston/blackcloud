"""Завантаження папки (або вибраних елементів) одним ZIP-архівом.

* Архів будується на льоту й одразу стрімиться: файли розшифровуються чанк за чанком,
  у пам'яті тримається не більше одного чанка (ZIP_STORED, без стиснення).
* Вихідний потік не підтримує seek, тому zipfile пише data descriptor після кожного
  запису; ZIP64 вмикається для великих файлів і великих архівів.
* Потрапляють лише файли, доступні для завантаження (перевірені антивірусом); решта
  перелічується в «_skipped.txt».
* Імена нормалізуються (без "..", абсолютних шляхів, керівних символів) і дедуплікуються.
* Ліміти (ZIP_MAX_BYTES, ZIP_MAX_FILES) перевіряються ДО початку стрімінгу → 400.
"""

import os
import time
import unicodedata
import zipfile
from dataclasses import dataclass

from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import StreamingHttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.exceptions import ValidationError

from . import services
from .models import File, Folder

SKIPPED_NAME = "_skipped.txt"
# Поріг, з якого запис примусово пишеться у форматі ZIP64
ZIP64_ENTRY_THRESHOLD = 1 << 31


@dataclass
class Entry:
    path: str
    file: File | None = None  # None — тека
    mtime: object = None


class Plan:
    def __init__(self):
        self.entries: list[Entry] = []
        self.skipped: list[tuple[str, str]] = []
        self.total_bytes = 0
        self._used: dict[str, set[str]] = {}

    @property
    def max_files(self) -> int:
        return int(getattr(settings, "ZIP_MAX_FILES", 10000))

    @property
    def max_bytes(self) -> int:
        return int(getattr(settings, "ZIP_MAX_BYTES", 10 * 1024**3))

    def _check_count(self):
        if len(self.entries) + len(self.skipped) > self.max_files:
            raise ValidationError(
                {"detail": _("Забагато файлів для ZIP-архіву (максимум %(n)s).") % {"n": self.max_files}}
            )

    def unique(self, parent: str, name: str) -> str:
        """Безпечне й унікальне (без урахування регістру) ім'я в межах теки архіву."""
        name = safe_component(name)
        used = self._used.setdefault(parent, set())
        candidate = name
        stem, ext = os.path.splitext(name)
        i = 1
        while candidate.casefold() in used:
            candidate = f"{stem} ({i}){ext}"
            i += 1
        used.add(candidate.casefold())
        return f"{parent}{candidate}"

    def add_dir(self, path: str, mtime=None):
        self.entries.append(Entry(path=path + "/", mtime=mtime))
        self._check_count()

    def add_file(self, parent: str, f: File):
        path = self.unique(parent, f.name)
        if not f.is_downloadable:
            self.skipped.append((path, f.status))
            self._check_count()
            return
        self.total_bytes += f.size
        if self.total_bytes > self.max_bytes:
            raise ValidationError(
                {"detail": _("Архів завеликий (максимум %(n)s ГБ). Завантажте частинами.") % {"n": round(self.max_bytes / 1024**3, 1)}}
            )
        self.entries.append(Entry(path=path, file=f, mtime=f.content_updated_at or f.updated_at))
        self._check_count()

    def add_folder(self, parent: str, folder: Folder):
        """Рекурсивно (пошарово, без рекурсії Python) додає папку з усім вмістом, крім кошика."""
        root_path = self.unique(parent, folder.name)
        self.add_dir(root_path, folder.updated_at)
        paths = {folder.pk: root_path + "/"}
        frontier = [folder.pk]
        depth = 0
        while frontier and depth <= services.MAX_DEPTH:
            depth += 1
            files = (
                File.objects.filter(owner_id=folder.owner_id, folder_id__in=frontier, deleted_at__isnull=True)
                .exclude(status=File.Status.UPLOADING)
                .order_by("name")
            )
            for f in files:
                self.add_file(paths[f.folder_id], f)
            children = Folder.objects.filter(
                owner_id=folder.owner_id, parent_id__in=frontier, deleted_at__isnull=True
            ).order_by("name")
            next_frontier = []
            for child in children:
                child_path = self.unique(paths[child.parent_id], child.name)
                self.add_dir(child_path, child.updated_at)
                paths[child.pk] = child_path + "/"
                next_frontier.append(child.pk)
            frontier = next_frontier

    def skipped_text(self) -> bytes:
        lines = [_("Ці файли не додано до архіву (ще не перевірені антивірусом або заблоковані):"), ""]
        lines += [f"{path}\t[{status}]" for path, status in self.skipped]
        return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def safe_component(name: str) -> str:
    name = unicodedata.normalize("NFC", str(name))
    name = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cc", "Cf"))
    name = name.replace("/", "_").replace("\\", "_").replace("\0", "").strip()
    if name in ("", ".", ".."):
        name = "_"
    return name[:255]


def _date_time(value) -> tuple:
    if value is None:
        value = timezone.now()
    t = timezone.localtime(value).timetuple() if timezone.is_aware(value) else value.timetuple()
    return max(t[:6], (1980, 1, 1, 0, 0, 0))


class _Sink:
    """Write-only буфер: zipfile пише сюди, генератор забирає накопичені байти."""

    def __init__(self):
        self.parts: list[bytes] = []

    def write(self, data) -> int:
        self.parts.append(bytes(data))
        return len(data)

    def flush(self):
        pass

    def take(self) -> bytes:
        data = b"".join(self.parts)
        self.parts.clear()
        return data


def iter_zip(plan: Plan, skipped_text: bytes | None):
    sink = _Sink()
    with zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for entry in plan.entries:
            info = zipfile.ZipInfo(entry.path, date_time=_date_time(entry.mtime))
            if entry.file is None:
                info.external_attr = (0o40755 << 16) | 0x10
                zf.writestr(info, b"")
                yield sink.take()
                continue
            f = entry.file
            info.compress_type = zipfile.ZIP_STORED
            info.file_size = f.size
            info.external_attr = 0o100644 << 16
            with zf.open(info, mode="w", force_zip64=f.size >= ZIP64_ENTRY_THRESHOLD) as dest:
                for chunk in services.iter_plaintext(f):
                    dest.write(chunk)
                    yield sink.take()
            yield sink.take()
        if skipped_text is not None:
            info = zipfile.ZipInfo(plan.unique("", SKIPPED_NAME), date_time=time.localtime()[:6])
            info.external_attr = 0o100644 << 16
            zf.writestr(info, skipped_text)
    yield sink.take()


async def _aiter(gen):
    """Під ASGI синхронний ітератор Django буферизує повністю; тут крок за кроком у потоці."""
    done = object()
    step = sync_to_async(lambda: next(gen, done), thread_sensitive=False)
    try:
        while True:
            part = await step()
            if part is done:
                break
            if part:
                yield part
    finally:
        await sync_to_async(gen.close, thread_sensitive=False)()


def build_plan(*, folders=(), files=()) -> Plan:
    plan = Plan()
    for folder in folders:
        plan.add_folder("", folder)
    for f in files:
        plan.add_file("", f)
    return plan


def zip_response(plan: Plan, filename: str) -> StreamingHttpResponse:
    # Текст _skipped.txt готуємо тут: мова активна лише в потоці запиту
    skipped = plan.skipped_text() if plan.skipped else None
    response = StreamingHttpResponse(_aiter(iter_zip(plan, skipped)), content_type="application/zip")
    services._attachment_headers(response, filename)
    # Проксі не повинні буферизувати потік
    response["X-Accel-Buffering"] = "no"
    return response
