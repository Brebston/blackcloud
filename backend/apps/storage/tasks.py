import hashlib
from datetime import timedelta
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.core.models import AuditLog
from apps.core.realtime import notify, push_to_user

from . import services, thumbnails
from .models import File, Folder, PublicLink
from .objectstore import get_store
from .scanner import ScanError, scan_stream

logger = logging.getLogger("blackcloud")


@shared_task(bind=True, max_retries=8, acks_late=True)
def scan_file(self, file_id: str):
    f = File.objects.select_related("owner").filter(pk=file_id).first()
    if f is None or f.status != File.Status.SCANNING:
        return

    digest = hashlib.sha256()

    def hashed_chunks():
        for chunk in services.iter_plaintext(f):
            digest.update(chunk)
            yield chunk

    try:
        if not settings.CLAMAV_ENABLED:
            for _ in hashed_chunks():
                pass
            status, detail = File.Status.CLEAN, "антивірус вимкнено"
        elif f.size > settings.CLAMAV_MAX_STREAM:
            for _ in hashed_chunks():
                pass
            status, detail = File.Status.UNSCANNED, "завеликий для антивірусної перевірки"
        else:
            clean, signature = scan_stream(hashed_chunks())
            status, detail = (File.Status.CLEAN, "") if clean else (File.Status.INFECTED, signature)
    except ScanError as exc:
        logger.warning("scan failed for %s: %s", file_id, exc)
        if self.request.retries >= self.max_retries:
            File.objects.filter(pk=f.pk).update(status=File.Status.FAILED, scan_detail=str(exc)[:200])
            return
        raise self.retry(countdown=min(30 * 2**self.request.retries, 1800))

    File.objects.filter(pk=f.pk, status=File.Status.SCANNING).update(
        status=status, scan_detail=detail, sha256=digest.hexdigest()
    )
    if status == File.Status.CLEAN and thumbnails.enabled():
        f.refresh_from_db()
        thumbnails.generate(f)
    push_to_user(f.owner_id, "file.updated", {"id": str(f.pk), "status": status})
    if status == File.Status.INFECTED:
        AuditLog.objects.create(
            user=f.owner, action="file.infected", target=str(f.pk), metadata={"name": f.name, "signature": detail}
        )
        notify(f.owner, "security", "Файл заблоковано антивірусом", f"«{f.name}»: {detail}", "/files")


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
    return len(ids)


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


@shared_task
def expire_public_links():
    PublicLink.objects.filter(expires_at__lt=timezone.now() - timedelta(days=30)).delete()


def delete_objects(prefix: str):
    get_store().delete_prefix(prefix)
