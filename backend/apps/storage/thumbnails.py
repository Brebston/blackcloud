"""Мініатюри для зображень, PDF і відео (кадр), а також великі зображення для перегляду HEIC/TIFF.

* Генеруються лише після успішної антивірусної перевірки (у Celery worker).
* Зберігаються зашифрованими тим самим ключем, що й файл (окремий AAD-індекс).
* Розбір файлів виконує окремий ізольований сервіс thumbnailer (без секретів і мережі);
  воркер лише розшифровує, надсилає байти й шифрує отриманий WebP.
* Захист від "декомпресійних бомб": обмеження розміру файлу та кількості пікселів,
  PDF рендериться окремим процесом pdftoppm з таймаутом.
"""

import logging
import shutil
import urllib.request

from django.conf import settings
from django.core.cache import cache

from . import crypto, thumbrender
from .models import File
from .objectstore import get_store

logger = logging.getLogger("blackcloud")

MAX_IMAGE_BYTES = 80 * 1024 * 1024
MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_VIDEO_BYTES = thumbrender.MAX_VIDEO_INPUT_BYTES
IMAGE_MIME = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "image/heic",
    "image/heif",
    "image/avif",
}
# Браузер не показує їх сам — для перегляду будуємо велике зображення (PREVIEW_SIZE)
PREVIEW_MIME = {"image/heic", "image/heif", "image/tiff", "image/bmp"}
VIDEO_MIME = {
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "video/x-matroska",
    "video/3gpp",
    "video/3gpp2",
    "video/x-msvideo",
    "video/x-m4v",
}
# Невдалі спроби (кеш): після стількох backfill більше не пробує цей вміст
MAX_ATTEMPTS = 3


def _kind(f: File) -> str | None:
    if f.mime_type in IMAGE_MIME:
        return "image" if f.size <= MAX_IMAGE_BYTES else None
    if f.mime_type == "application/pdf":
        # pdftoppm є в образі; у режимі сервісу перевіряє вже thumbnailer
        ok = f.size <= MAX_PDF_BYTES and (bool(settings.THUMBNAILER_URL) or shutil.which("pdftoppm") is not None)
        return "pdf" if ok else None
    if f.mime_type in VIDEO_MIME:
        ok = 0 < f.size <= MAX_VIDEO_BYTES and (bool(settings.THUMBNAILER_URL) or shutil.which("ffmpeg") is not None)
        return "video" if ok else None
    return None


def supports(f: File) -> bool:
    return _kind(f) is not None


def wants_preview(f: File) -> bool:
    return f.mime_type in PREVIEW_MIME and f.size <= MAX_IMAGE_BYTES


def _post(kind: str, body, length: int) -> bytes:
    url = settings.THUMBNAILER_URL.rstrip("/") + f"/render?kind={kind}"
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/octet-stream", "Content-Length": str(length)},
    )
    with urllib.request.urlopen(request, timeout=180) as resp:  # noqa: S310 — адреса з налаштувань
        out = resp.read(thumbrender.MAX_OUTPUT_BYTES + 1)
    if not thumbrender.is_webp(out):
        raise ValueError("thumbnailer returned invalid data")
    return out


def _render_remote(kind: str, data: bytes) -> bytes:
    return _post(kind, data, len(data))


def render(kind: str, data: bytes) -> bytes:
    if settings.THUMBNAILER_URL:
        return _render_remote(kind, data)
    return thumbrender.render(kind, data)


def _render_video(f: File) -> bytes:
    from .services import iter_plaintext

    if settings.THUMBNAILER_URL:
        # Потоком, без збирання всього відео в пам'яті worker-а
        return _post("video", iter_plaintext(f), f.size)
    import os
    import tempfile

    with tempfile.TemporaryDirectory(prefix="bc-video-") as tmp:
        path = os.path.join(tmp, "in.video")
        with open(path, "wb") as fh:
            for chunk in iter_plaintext(f):
                fh.write(chunk)
        return thumbrender.render_video_file(path)


def _attempt_key(f: File) -> str:
    return f"thumb-fail:{f.pk}:{f.content_version}"


def attempts_exhausted(f: File) -> bool:
    return (cache.get(_attempt_key(f)) or 0) >= MAX_ATTEMPTS


def _store(f: File, index: int, key: str, webp: bytes) -> None:
    data_key = crypto.unwrap_key(f.wrapped_key, f.key_version, f.id)
    get_store().put(key, crypto.encrypt_chunk(data_key, f.id, index, webp))


def generate(f: File) -> bool:
    """Створює мініатюру (і для HEIC/TIFF/BMP — велике зображення для перегляду).
    Повертає True, якщо мініатюра є."""
    from .services import iter_plaintext

    kind = _kind(f)
    if kind is None:
        return False
    try:
        if kind == "video":
            thumb, preview = _render_video(f), None
        else:
            data = b"".join(iter_plaintext(f))
            thumb = render(kind, data)
            preview = render("preview", data) if kind == "image" and wants_preview(f) else None
    except Exception as exc:  # пошкоджений, непідтримуваний файл або сервіс зайнятий
        key = _attempt_key(f)
        cache.set(key, (cache.get(key) or 0) + 1, 7 * 24 * 3600)
        logger.info("thumbnail skipped for %s (%s): %s", f.pk, kind, exc.__class__.__name__)
        return False
    _store(f, crypto.THUMB_INDEX, f.thumb_key(), thumb)
    fields = {"has_thumbnail": True}
    if preview is not None:
        _store(f, crypto.PREVIEW_INDEX, f.preview_key(), preview)
        fields["has_preview"] = True
    File.objects.filter(pk=f.pk, content_version=f.content_version).update(**fields)
    return True


def enabled() -> bool:
    return getattr(settings, "THUMBNAILS_ENABLED", True)
