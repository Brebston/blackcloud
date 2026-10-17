"""Обране (зірочки), особисті кольорові теги й «Нещодавні» для «Файлів».

Усе особисте: зірочки й теги бачить лише той, хто їх поставив, навіть на чужих
файлах, до яких має спільний доступ. «Нещодавні» — файли, які користувач відкривав
(перегляд, завантаження, редактор), а також власні нещодавно завантажені чи змінені.
"""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import macmeta, services
from .models import File, FileAccess, Folder, ItemTag, Star, Tag

RECENT_DAYS = 30
RECENT_MAX = 100


def record_access(user, f: File) -> None:
    """Позначка «відкривав» (для «Нещодавніх»); помилки не заважають основній дії."""
    if user is None or not getattr(user, "is_authenticated", False):
        return
    now = timezone.now()
    try:
        if not FileAccess.objects.filter(user=user, file=f).update(accessed_at=now):
            with transaction.atomic():
                FileAccess.objects.create(user=user, file=f, accessed_at=now)
    except IntegrityError:
        FileAccess.objects.filter(user=user, file=f).update(accessed_at=now)


def readable_folder(user, pk) -> Folder:
    from rest_framework.exceptions import NotFound

    folder = Folder.objects.filter(pk=pk, deleted_at__isnull=True).first()
    if folder is None:
        raise NotFound()
    if folder.owner_id == user.pk:
        if services.is_in_trash(folder):
            raise NotFound()
    elif services.shared_folder_root(user, folder) is None:
        raise NotFound()
    return folder


def readable_file(user, pk) -> File:
    from rest_framework.exceptions import NotFound

    f = services.get_readable_file(user, pk)
    if f.deleted_at is not None or f.status == File.Status.UPLOADING:
        raise NotFound()
    return f


def _visible(user, f: File) -> bool:
    if f.deleted_at is not None or f.status == File.Status.UPLOADING or macmeta.is_system_name(f.name):
        return False
    if f.owner_id == user.pk:
        return not services.is_in_trash(f.folder)
    return services.can_read_file(user, f)


def _visible_folder(user, folder: Folder) -> bool:
    if folder.deleted_at is not None:
        return False
    if folder.owner_id == user.pk:
        return not services.is_in_trash(folder)
    return services.shared_folder_root(user, folder) is not None


def decorations(user, folder_ids, file_ids) -> dict:
    """{"starred": {id,…}, "tags": {id: [tag_id,…]}} для позначок у списку файлів."""
    folder_ids, file_ids = list(folder_ids), list(file_ids)
    starred = {str(i) for i in Star.objects.filter(owner=user, folder_id__in=folder_ids).values_list("folder_id", flat=True)}
    starred |= {str(i) for i in Star.objects.filter(owner=user, file_id__in=file_ids).values_list("file_id", flat=True)}
    tags: dict[str, list[str]] = {}
    rows = ItemTag.objects.filter(tag__owner=user).filter(folder_id__in=folder_ids) | ItemTag.objects.filter(tag__owner=user, file_id__in=file_ids)
    for item_folder, item_file, tag_id in rows.values_list("folder_id", "file_id", "tag_id"):
        tags.setdefault(str(item_folder or item_file), []).append(str(tag_id))
    return {"starred": starred, "tags": tags}


def decorate(user, data: dict) -> dict:
    """Додає starred/tags до folders[] і files[] відповіді переглядача."""
    folders, files = data.get("folders") or [], data.get("files") or []
    d = decorations(user, [x["id"] for x in folders], [x["id"] for x in files])
    for item in [*folders, *files]:
        key = str(item["id"])
        item["starred"] = key in d["starred"]
        item["tags"] = d["tags"].get(key, [])
    return data


def set_star(user, *, file=None, folder=None, starred: bool) -> None:
    target = {"file": file} if file is not None else {"folder": folder}
    if not starred:
        Star.objects.filter(owner=user, **target).delete()
        return
    try:
        with transaction.atomic():
            Star.objects.get_or_create(owner=user, **target)
    except IntegrityError:
        pass


def starred_items(user):
    folders = [
        s.folder for s in Star.objects.filter(owner=user, folder__isnull=False).select_related("folder").order_by("folder__name")
        if _visible_folder(user, s.folder)
    ]
    files = [
        s.file for s in Star.objects.filter(owner=user, file__isnull=False).select_related("file", "file__folder", "file__owner").order_by("file__name")
        if _visible(user, s.file)
    ]
    return folders, files


def tagged_items(user, tag: Tag):
    rows = ItemTag.objects.filter(tag=tag).select_related("folder", "file", "file__folder", "file__owner")
    folders = sorted((r.folder for r in rows if r.folder_id and _visible_folder(user, r.folder)), key=lambda x: x.name.lower())
    files = sorted((r.file for r in rows if r.file_id and _visible(user, r.file)), key=lambda x: x.name.lower())
    return folders, files


def assign_tag(tag: Tag, *, files=(), folders=(), assign: bool) -> int:
    if not assign:
        n = ItemTag.objects.filter(tag=tag, file__in=list(files)).delete()[0]
        return n + ItemTag.objects.filter(tag=tag, folder__in=list(folders)).delete()[0]
    count = 0
    for target in [{"file": f} for f in files] + [{"folder": f} for f in folders]:
        try:
            with transaction.atomic():
                _obj, created = ItemTag.objects.get_or_create(tag=tag, **target)
                count += int(created)
        except IntegrityError:
            pass
    return count


def recent_files(user, limit: int = 60):
    """[(file, коли, чому)] — найсвіжіші першими. Чому: opened | uploaded | edited."""
    since = timezone.now() - timedelta(days=RECENT_DAYS)
    events: dict = {}

    def bump(file_id, when, reason):
        if when is None:
            return
        cur = events.get(file_id)
        if cur is None or when > cur[0]:
            events[file_id] = (when, reason)

    for file_id, when in FileAccess.objects.filter(user=user, accessed_at__gte=since).order_by("-accessed_at").values_list("file_id", "accessed_at")[:RECENT_MAX * 2]:
        bump(file_id, when, "opened")
    own = File.objects.filter(owner=user, deleted_at__isnull=True, created_at__gte=since).order_by("-created_at")
    for file_id, when in own.values_list("pk", "created_at")[:RECENT_MAX * 2]:
        bump(file_id, when, "uploaded")
    edited = File.objects.filter(owner=user, deleted_at__isnull=True, content_updated_at__gte=since).order_by("-content_updated_at")
    for file_id, when in edited.values_list("pk", "content_updated_at")[:RECENT_MAX]:
        bump(file_id, when, "edited")
    ordered = sorted(events.items(), key=lambda kv: kv[1][0], reverse=True)[: limit * 2]
    files = {f.pk: f for f in File.objects.filter(pk__in=[k for k, _v in ordered]).select_related("folder", "owner")}
    out = []
    for file_id, (when, reason) in ordered:
        f = files.get(file_id)
        if f is not None and _visible(user, f):
            out.append((f, when, reason))
        if len(out) >= limit:
            break
    return out
