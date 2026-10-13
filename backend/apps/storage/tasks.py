import hashlib
from datetime import timedelta
import logging

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.translation import gettext_noop

from apps.core.models import AuditLog
from apps.core.realtime import notify, push_to_user

from . import services, thumbnails
from .models import File, FileVersion, Folder, PublicLink
from .objectstore import get_store
from .scanner import ScanError, scan_stream

logger = logging.getLogger("blackcloud")


def _scan_chunks(chunks, size):
    """Перевіряє потік відкритого тексту. Повертає (status, detail, sha256). Кидає ScanError."""
    digest = hashlib.sha256()

    def hashed_chunks():
        for chunk in chunks:
            digest.update(chunk)
            yield chunk

    if not settings.CLAMAV_ENABLED:
        for _ in hashed_chunks():
            pass
        status, detail = File.Status.CLEAN, "антивірус вимкнено"
    elif size > settings.CLAMAV_MAX_STREAM:
        for _ in hashed_chunks():
            pass
        status, detail = File.Status.UNSCANNED, "завеликий для антивірусної перевірки"
    else:
        clean, signature = scan_stream(hashed_chunks())
        status, detail = (File.Status.CLEAN, "") if clean else (File.Status.INFECTED, signature)
    return status, detail, digest.hexdigest()


def _notify_infected(f, detail):
    AuditLog.objects.create(
        user=f.owner, action="file.infected", target=str(f.pk), metadata={"name": f.name, "signature": detail}
    )
    notify(
        f.owner,
        "security",
        gettext_noop("Файл заблоковано антивірусом"),
        gettext_noop("«%(name)s»: %(detail)s"),
        "/files",
        params={"name": f.name, "detail": detail},
    )


def _scan_and_store(f: File, version: int, *, inline: bool = False) -> None:
    """Перевіряє вміст версії `version` файлу і записує результат. Кидає ScanError.

    inline=True — виклик з веб-процесу: мініатюру не будуємо тут (thumbnailer доступний
    лише worker-у), а ставимо окреме завдання.
    """
    status, detail, sha = _scan_chunks(services.iter_plaintext(f), f.size)
    updated = File.objects.filter(pk=f.pk, status=File.Status.SCANNING, content_version=version).update(
        status=status, scan_detail=detail, sha256=sha
    )
    if not updated:
        # Поки йшла перевірка, вміст став архівною версією — результат належить їй
        FileVersion.objects.filter(file=f, number=version, status=File.Status.SCANNING).update(
            status=status, scan_detail=detail, sha256=sha
        )
    elif status == File.Status.CLEAN and thumbnails.enabled():
        if inline:
            file_id = str(f.pk)
            transaction.on_commit(lambda: generate_thumbnail.delay(file_id))
        else:
            f.refresh_from_db()
            thumbnails.generate(f)
    push_to_user(f.owner_id, "file.updated", {"id": str(f.pk), "status": status})
    if status == File.Status.INFECTED:
        _notify_infected(f, detail)


@shared_task(bind=True, max_retries=8, acks_late=True)
def scan_file(self, file_id: str):
    f = File.objects.select_related("owner").filter(pk=file_id).first()
    if f is None or f.status != File.Status.SCANNING:
        return
    # Знімок версії: якщо під час перевірки вміст замінять, результат не потрапить
    # на новий (неперевірений) вміст — лише на ту версію, яку справді перевірено
    version = f.content_version
    try:
        _scan_and_store(f, version)
    except ScanError as exc:
        logger.warning("scan failed for %s: %s", file_id, exc)
        if self.request.retries >= self.max_retries:
            File.objects.filter(pk=f.pk, status=File.Status.SCANNING, content_version=version).update(
                status=File.Status.FAILED, scan_detail=str(exc)[:200]
            )
            return
        raise self.retry(countdown=min(30 * 2**self.request.retries, 1800))
    except (FileNotFoundError, KeyError):
        # Версію видалили/замінили під час перевірки — нову версію перевірить окреме завдання
        logger.info("scan of %s v%s skipped: content gone", file_id, version)
        return


def scan_small_file_now(file_id: str) -> None:
    """Невеликі файли (до SCAN_INLINE_MAX_BYTES) перевіряємо одразу у веб-процесі.

    Finder після запису службового ._-файлу одразу читає його назад; поки файл
    у черзі антивірусу, GET повертає 403 і Finder перериває копіювання (помилка 100004).
    Перевірка невеликого файлу триває мілісекунди. Правило не змінюється: файл стає
    доступним лише після перевірки. Якщо clamd недоступний — звичайна черга.
    """
    f = File.objects.select_related("owner").filter(pk=file_id).first()
    if f is None or f.status != File.Status.SCANNING:
        return
    if f.size > settings.SCAN_INLINE_MAX_BYTES:
        scan_file.delay(file_id)
        return
    try:
        _scan_and_store(f, f.content_version, inline=True)
    except Exception as exc:  # noqa: BLE001 — будь-яка проблема: перевірку виконає worker
        logger.warning("inline scan of %s failed, queued: %s", file_id, exc)
        scan_file.delay(file_id)


@shared_task(acks_late=True)
def generate_thumbnail(file_id: str):
    f = File.objects.filter(pk=file_id, status=File.Status.CLEAN).first()
    if f is not None and thumbnails.enabled():
        thumbnails.generate(f)


@shared_task(bind=True, max_retries=8, acks_late=True)
def scan_version(self, version_id: str):
    """Антивірус для архівної версії, яку замінили ще до завершення перевірки."""
    v = FileVersion.objects.select_related("file", "file__owner").filter(pk=version_id).first()
    if v is None or v.status != File.Status.SCANNING:
        return
    try:
        status, detail, sha = _scan_chunks(services.iter_version_plaintext(v), v.size)
    except ScanError as exc:
        logger.warning("scan failed for version %s: %s", version_id, exc)
        if self.request.retries >= self.max_retries:
            FileVersion.objects.filter(pk=v.pk, status=File.Status.SCANNING).update(
                status=File.Status.FAILED, scan_detail=str(exc)[:200]
            )
            return
        raise self.retry(countdown=min(30 * 2**self.request.retries, 1800))
    except (FileNotFoundError, KeyError):
        return
    FileVersion.objects.filter(pk=v.pk, status=File.Status.SCANNING).update(status=status, scan_detail=detail, sha256=sha)
    if status == File.Status.INFECTED:
        _notify_infected(v.file, detail)


@shared_task
def requeue_stuck_scans():
    """Повторно ставить у чергу файли, що надто довго чекають антивірус
    (наприклад, якщо ClamAV був недоступний і ретраї вичерпались або загубились)."""
    stale = timezone.now() - timedelta(minutes=10)
    ids = list(
        File.objects.filter(status=File.Status.SCANNING, updated_at__lt=stale).values_list("pk", flat=True)[:500]
    )
    for pk in ids:
        File.objects.filter(pk=pk).update(updated_at=timezone.now())
        scan_file.delay(str(pk))
    version_ids = list(
        FileVersion.objects.filter(status=File.Status.SCANNING, archived_at__lt=stale).values_list("pk", flat=True)[:100]
    )
    for pk in version_ids:
        scan_version.delay(str(pk))
    return len(ids) + len(version_ids)


@shared_task
def backfill_thumbnails(limit: int = 5000) -> int:
    """Мініатюри для файлів, завантажених до появи цієї функції."""
    done = 0
    qs = File.objects.filter(status=File.Status.CLEAN, has_thumbnail=False, deleted_at__isnull=True)
    for f in qs.iterator():
        if done >= limit:
            break
        if thumbnails.supports(f) and thumbnails.generate(f):
            done += 1
    return done


@shared_task
def purge_expired_trash():
    cutoff = services.trash_cutoff()
    for f in File.objects.filter(deleted_at__lt=cutoff).select_related("owner").iterator():
        try:
            services.purge_file(f)
        except Exception:
            logger.exception("purge failed for file %s", f.pk)
    for folder in Folder.objects.filter(deleted_at__lt=cutoff).iterator():
        try:
            services.purge_folder(folder)
        except Exception:
            logger.exception("purge failed for folder %s", folder.pk)


@shared_task
def cleanup_stale_uploads():
    stale = File.objects.filter(status=File.Status.UPLOADING, upload_expires_at__lt=timezone.now())
    for f in stale.select_related("owner").iterator():
        services.purge_file(f)
    for f in File.objects.filter(status=File.Status.FAILED, updated_at__lt=timezone.now() - timedelta(days=7)):
        services.purge_file(f)
    # Незавершені завантаження нових версій
    stale_versions = FileVersion.objects.filter(status=File.Status.UPLOADING, upload_expires_at__lt=timezone.now())
    for v in stale_versions.select_related("file").iterator():
        try:
            services.abort_version_upload(v)
        except Exception:
            logger.exception("failed to drop stale version upload %s", v.pk)


@shared_task
def prune_file_versions():
    """Ретеншн версій: не більше FILE_MAX_VERSIONS на файл і не старші FILE_VERSIONS_MAX_AGE_DAYS."""
    days = int(getattr(settings, "FILE_VERSIONS_MAX_AGE_DAYS", 0) or 0)
    older_than = timezone.now() - timedelta(days=days) if days > 0 else None
    archived = FileVersion.objects.exclude(status=File.Status.UPLOADING)
    over_limit = (
        archived.order_by().values("file_id").annotate(n=Count("pk")).filter(n__gt=services.max_versions() - 1).values_list("file_id", flat=True)
    )
    file_ids = set(over_limit[:5000])
    if older_than is not None:
        file_ids |= set(archived.filter(archived_at__lt=older_than).order_by().values_list("file_id", flat=True).distinct()[:5000])
    removed = 0
    for f in File.objects.filter(pk__in=file_ids).iterator():
        try:
            removed += services.prune_versions(f, older_than=older_than)
        except Exception:
            logger.exception("version prune failed for %s", f.pk)
    return removed


@shared_task
def expire_public_links():
    PublicLink.objects.filter(expires_at__lt=timezone.now() - timedelta(days=30)).delete()


def delete_objects(prefix: str):
    get_store().delete_prefix(prefix)
