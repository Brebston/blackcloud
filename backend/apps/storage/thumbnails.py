"""Мініатюри для зображень і PDF.

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

from . import crypto, thumbrender
from .models import File
from .objectstore import get_store

logger = logging.getLogger("blackcloud")

MAX_IMAGE_BYTES = 80 * 1024 * 1024
MAX_PDF_BYTES = 100 * 1024 * 1024
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


def supports(f: File) -> bool:
    if f.mime_type in IMAGE_MIME:
        return f.size <= MAX_IMAGE_BYTES
    if f.mime_type == "application/pdf":
        # pdftoppm є в образі; у режимі сервісу перевіряє вже thumbnailer
        return f.size <= MAX_PDF_BYTES and (bool(settings.THUMBNAILER_URL) or shutil.which("pdftoppm") is not None)
    return False


def _render_remote(kind: str, data: bytes) -> bytes:
    url = settings.THUMBNAILER_URL.rstrip("/") + f"/render?kind={kind}"
    request = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/octet-stream"}
    )
    with urllib.request.urlopen(request, timeout=90) as resp:  # noqa: S310 — адреса з налаштувань
        out = resp.read(thumbrender.MAX_OUTPUT_BYTES + 1)
    if not thumbrender.is_webp(out):
        raise ValueError("thumbnailer returned invalid data")
    return out


def render(kind: str, data: bytes) -> bytes:
    if settings.THUMBNAILER_URL:
        return _render_remote(kind, data)
    return thumbrender.render(kind, data)


def generate(f: File) -> bool:
    """Створює мініатюру. Повертає True, якщо вдалося."""
    from .services import iter_plaintext

    if not supports(f):
        return False
    try:
        data = b"".join(iter_plaintext(f))
        webp = render("pdf" if f.mime_type == "application/pdf" else "image", data)
    except Exception as exc:  # пошкоджений або підозрілий файл — просто без мініатюри
        logger.info("thumbnail skipped for %s: %s", f.pk, exc.__class__.__name__)
        return False
    data_key = crypto.unwrap_key(f.wrapped_key, f.key_version, f.id)
    get_store().put(f.thumb_key(), crypto.encrypt_chunk(data_key, f.id, crypto.THUMB_INDEX, webp))
    File.objects.filter(pk=f.pk, content_version=f.content_version).update(has_thumbnail=True)
    return True


def enabled() -> bool:
    return getattr(settings, "THUMBNAILS_ENABLED", True)
