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
MAX_PIXELS = 40_000_000
MAX_INPUT_BYTES = 100 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024


def _pil():
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:  # HEIC/HEIF з iPhone
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except Exception:
        pass
    return Image, ImageOps


def _to_webp(image) -> bytes:
    Image, ImageOps = _pil()
    image = ImageOps.exif_transpose(image)
    image.thumbnail((THUMB_SIZE, THUMB_SIZE))
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    out = io.BytesIO()
    image.save(out, format="WEBP", quality=80, method=4)
    return out.getvalue()


def render_image(data: bytes) -> bytes:
    Image, _ = _pil()
    with warnings.catch_warnings():
        # «Декомпресійна бомба» — помилка, а не попередження
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if width * height > MAX_PIXELS:
                raise ValueError("image too large")
            img.seek(0)  # перший кадр GIF/TIFF
            img.load()
            return _to_webp(img.copy())


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
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("input too large")
    if kind == "pdf":
        out = render_pdf(data)
    elif kind == "image":
        out = render_image(data)
    else:
        raise ValueError("unknown kind")
    if not is_webp(out):
        raise ValueError("bad output")
    return out


def is_webp(data: bytes) -> bool:
    return 12 < len(data) <= MAX_OUTPUT_BYTES and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
