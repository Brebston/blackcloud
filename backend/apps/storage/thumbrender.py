"""Рендер мініатюр (без Django і без доступу до ключів).

Цей модуль виконується в ізольованому сервісі `thumbnailer` (thumbsvc.py): контейнер
без секретів, без виходу в мережу, з read-only ФС і лімітом пам'яті. Розбір
зображень і PDF — найризикованіший код (C-бібліотеки декодерів), тож навіть у
разі експлойта зловмисник не отримає ані ключів шифрування, ані БД.
"""

import io
import os
import subprocess
import tempfile
import warnings

THUMB_SIZE = 480
# Перегляд у браузері форматів, які він не показує сам (HEIC/HEIF, TIFF, BMP): досить
# великий, щоб на Retina-екрані фото виглядало чітко, але без передачі оригіналу
PREVIEW_SIZE = 2560
AVATAR_SIZE = 256
# 48-Мп знімки iPhone Pro — 8064×6048 ≈ 48,8 млн пікселів
MAX_PIXELS = 64_000_000
MAX_INPUT_BYTES = 100 * 1024 * 1024
# Відео не читається в пам'ять: сервіс пише його у тимчасовий файл
MAX_VIDEO_INPUT_BYTES = 300 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
FFMPEG_TIMEOUT = 45
# Лише локальний файл і лише контейнери відео: ffmpeg не відкриє посилання/плейлисти
# всередині файлу (HLS/concat з http:// чи file:///etc/... — відомий клас атак)
VIDEO_FORMATS = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi"


def _pil():
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:  # HEIC/HEIF з iPhone
        import pillow_heif

        # Менше потоків декодера — менше віртуальної пам'яті під RLIMIT_AS сервісу
        pillow_heif.options.DECODE_THREADS = 2
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    return Image, ImageOps


def _encode(image, size: int, quality: int) -> bytes:
    Image, ImageOps = _pil()
    image = ImageOps.exif_transpose(image)
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    out = io.BytesIO()
    image.save(out, format="WEBP", quality=quality, method=4)
    return out.getvalue()


def _to_webp(image) -> bytes:
    return _encode(image, THUMB_SIZE, 80)


def _open(data: bytes, target: int):
    """Відкриває зображення з перевірками; JPEG одразу декодується в зменшеному масштабі."""
    Image, _ = _pil()
    with warnings.catch_warnings():
        # «Декомпресійна бомба» — помилка, а не попередження
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if width * height > MAX_PIXELS:
                raise ValueError("image too large")
            img.seek(0)  # перший кадр GIF/TIFF
            if img.format == "JPEG":
                img.draft("RGB", (target, target))  # менше пам'яті й часу для великих JPEG
            img.load()
            return img.copy()


def render_image(data: bytes) -> bytes:
    return _encode(_open(data, THUMB_SIZE), THUMB_SIZE, 80)


def render_preview(data: bytes) -> bytes:
    return _encode(_open(data, PREVIEW_SIZE), PREVIEW_SIZE, 82)


def render_video_file(path: str) -> bytes:
    """Кадр відео (на 1-й секунді, для коротких — перший) → мініатюра WebP."""
    Image, _ = _pil()
    with tempfile.TemporaryDirectory(prefix="bc-video-") as tmp:
        out = os.path.join(tmp, "frame.png")
        for seek in ("1", "0"):
            cmd = [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-protocol_whitelist", "file", "-format_whitelist", VIDEO_FORMATS,
                "-threads", "2", "-ss", seek, "-i", path,
                "-frames:v", "1", "-vf", f"scale='min({THUMB_SIZE * 2},iw)':-2",
                "-an", "-sn", "-dn", "-f", "image2", "-c:v", "png", "-y", out,
            ]  # fmt: skip
            subprocess.run(cmd, check=False, timeout=FFMPEG_TIMEOUT, capture_output=True)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                break
        else:
            raise ValueError("no video frame")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(out) as img:
                img.load()
                return _to_webp(img.copy())


def render_video(data: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="bc-video-") as tmp:
        src = os.path.join(tmp, "in.video")
        fd = os.open(src, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return render_video_file(src)


def render_avatar(data: bytes) -> bytes:
    """Фото профілю: квадрат AVATAR_SIZE×AVATAR_SIZE (центральна частина), WebP без EXIF."""
    Image, ImageOps = _pil()
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if width * height > MAX_PIXELS:
                raise ValueError("image too large")
            img.seek(0)
            img.load()
            image = ImageOps.exif_transpose(img.copy())
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    image = ImageOps.fit(image, (AVATAR_SIZE, AVATAR_SIZE), method=Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="WEBP", quality=85, method=4)  # нове зображення — метадані не переносяться
    return out.getvalue()


def render_pdf(data: bytes) -> bytes:
    Image, _ = _pil()
    with tempfile.TemporaryDirectory(prefix="bc-thumb-") as tmp:
        src = os.path.join(tmp, "in.pdf")
        fd = os.open(src, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        out_base = os.path.join(tmp, "page")
        subprocess.run(
            ["pdftoppm", "-png", "-f", "1", "-l", "1", "-singlefile", "-scale-to", str(THUMB_SIZE * 2), src, out_base],
            check=True,
            timeout=30,
            capture_output=True,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(out_base + ".png") as img:
                img.load()
                return _to_webp(img.copy())


def render(kind: str, data: bytes) -> bytes:
    if len(data) > (MAX_VIDEO_INPUT_BYTES if kind == "video" else MAX_INPUT_BYTES):
        raise ValueError("input too large")
    if kind == "pdf":
        out = render_pdf(data)
    elif kind == "image":
        out = render_image(data)
    elif kind == "preview":
        out = render_preview(data)
    elif kind == "video":
        out = render_video(data)
    elif kind == "avatar":
        out = render_avatar(data)
    else:
        raise ValueError("unknown kind")
    if not is_webp(out):
        raise ValueError("bad output")
    return out


def is_webp(data: bytes) -> bool:
    return 12 < len(data) <= MAX_OUTPUT_BYTES and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
