from celery import shared_task
from django.core.cache import cache

LOCK_KEY = "photos:index-lock"


@shared_task
def index_photos(limit: int = 300) -> dict:
    """Щодесять хвилин: нові фото/відео в стрічку й читання EXIF (по limit файлів за раз)."""
    if not cache.add(LOCK_KEY, 1, 1800):
        return {"skipped": True}
    try:
        from .indexing import run

        return run(limit)
    finally:
        cache.delete(LOCK_KEY)


@shared_task(acks_late=True)
def index_photo(file_id: str) -> None:
    from apps.storage.models import File

    from .indexing import index_file, kind_of

    f = File.objects.select_related("owner").filter(pk=file_id, status=File.Status.CLEAN, deleted_at__isnull=True).first()
    if f is not None and kind_of(f):
        index_file(f)
