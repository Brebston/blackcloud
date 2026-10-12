"""Відображення шляхів /dav/<папка>/<файл> на дерево користувача (лише власні,
не в кошику) і назад — у href з відсотковим кодуванням (UTF-8)."""

import re
import unicodedata
import urllib.parse
from dataclasses import dataclass

from apps.storage.models import File, Folder
from apps.storage.services import MAX_DEPTH

PREFIX = "/dav/"
_ENCODED_SEP = re.compile(r"%(2f|5c|00)", re.IGNORECASE)


class BadPath(ValueError):
    pass


def check_raw(raw: str | bytes | None) -> None:
    """Закодовані роздільники (%2F, %5C) і NUL у сирому шляху — відхиляємо."""
    if raw is None:
        return
    if isinstance(raw, bytes):
        raw = raw.decode("latin-1")
    if _ENCODED_SEP.search(raw):
        raise BadPath("encoded separator")


def raw_path(request) -> str | None:
    scope = getattr(request, "scope", None)
    if isinstance(scope, dict) and scope.get("raw_path"):
        return scope["raw_path"]
    return request.META.get("REQUEST_URI") or request.META.get("RAW_URI")


def split(path: str) -> tuple[str, ...]:
    """Вже декодований шлях (після /dav/) → кортеж сегментів у NFC.

    «.», «..», порожні сегменти посередині, керівні символи й «\\» — помилка."""
    if path is None:
        return ()
    if len(path) > 4096:
        raise BadPath("too long")
    parts = path.strip("/").split("/") if path.strip("/") else []
    result = []
    for seg in parts:
        if seg in ("", ".", "..") or "\\" in seg:
            raise BadPath("bad segment")
        if any(unicodedata.category(ch) == "Cc" for ch in seg):
            raise BadPath("control character")
        # macOS надсилає імена в NFD; у БД вони зберігаються в NFC
        result.append(unicodedata.normalize("NFC", seg))
    if len(result) > MAX_DEPTH:
        raise BadPath("too deep")
    return tuple(result)


def key(segments: tuple[str, ...]) -> str:
    return "/".join(segments)


def href(segments: tuple[str, ...], collection: bool) -> str:
    path = PREFIX + "/".join(urllib.parse.quote(s, safe="") for s in segments)
    if collection and segments:
        path += "/"
    return path


@dataclass
class Node:
    segments: tuple[str, ...]
    folder: Folder | None = None  # колекція (None + is_root → корінь)
    file: File | None = None
    is_root: bool = False
    parent: Folder | None = None
    parent_missing: bool = False

    @property
    def exists(self) -> bool:
        return self.is_root or self.folder is not None or self.file is not None

    @property
    def is_collection(self) -> bool:
        return self.is_root or self.folder is not None

    @property
    def name(self) -> str:
        return self.segments[-1] if self.segments else ""

    @property
    def parent_segments(self) -> tuple[str, ...]:
        return self.segments[:-1]

    @property
    def href(self) -> str:
        return href(self.segments, self.is_collection)

    @property
    def key(self) -> str:
        return key(self.segments)


def visible_files(qs):
    """Файли, видимі через WebDAV: не в кошику, не незавершені й не зіпсовані."""
    return qs.filter(deleted_at__isnull=True).exclude(status__in=[File.Status.UPLOADING, File.Status.FAILED])


def child_folder(user, parent: Folder | None, name: str) -> Folder | None:
    return Folder.objects.filter(owner=user, parent=parent, name=name, deleted_at__isnull=True).first()


def child_file(user, parent: Folder | None, name: str) -> File | None:
    return visible_files(File.objects.filter(owner=user, folder=parent, name=name)).order_by("-created_at").first()


def resolve_folder(user, segments: tuple[str, ...]) -> tuple[bool, Folder | None]:
    """(знайдено, папка); корінь — (True, None)."""
    current = None
    for seg in segments:
        current = child_folder(user, current, seg)
        if current is None:
            return False, None
    return True, current


def resolve(user, segments: tuple[str, ...]) -> Node:
    if not segments:
        return Node(segments, is_root=True)
    found, parent = resolve_folder(user, segments[:-1])
    node = Node(segments, parent=parent, parent_missing=not found)
    if not found:
        return node
    folder = child_folder(user, parent, segments[-1])
    if folder is not None:
        node.folder = folder
        return node
    node.file = child_file(user, parent, segments[-1])
    return node


def _host(netloc: str, scheme: str) -> str:
    """Хост без стандартного порту (https://host:443/ == https://host/)."""
    netloc = netloc.lower().rsplit("@", 1)[-1]
    default = ":443" if scheme == "https" else ":80"
    return netloc[: -len(default)] if netloc.endswith(default) else netloc


def parse_destination(request, value: str) -> tuple[str, ...]:
    """Destination: той самий хост і префікс /dav/."""
    if not value or len(value) > 8192:
        raise BadPath("missing destination")
    parts = urllib.parse.urlsplit(value)
    if parts.scheme or parts.netloc:
        if parts.scheme not in ("http", "https"):
            raise BadPath("bad scheme")
        if _host(parts.netloc, parts.scheme) != _host(request.get_host(), parts.scheme):
            raise BadPath("other host")
    raw = parts.path
    check_raw(raw)
    path = urllib.parse.unquote(raw, encoding="utf-8", errors="strict")
    if path.rstrip("/") == PREFIX.rstrip("/"):
        return ()
    if not path.startswith(PREFIX):
        raise BadPath("outside /dav/")
    return split(path[len(PREFIX) :])
