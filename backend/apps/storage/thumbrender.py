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


# ─────────────────────────── Метадані фото й відео (галерея «Фото») ───────────────────────────
#
# Повертає лише прості значення (рядки обмеженої довжини, числа): дата зйомки, камера,
# об'єктив, розміри, тривалість, координати. Розбір — тут, в ізольованому сервісі, як і декодування.

META_TEXT_MAX = 100
FFPROBE_TIMEOUT = 30


def _text(value) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value if ch.isprintable()).strip().strip("\x00")[:META_TEXT_MAX]


def _ratio(value) -> float | None:
    try:
        if isinstance(value, tuple) and len(value) == 2:
            return float(value[0]) / float(value[1]) if value[1] else None
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _dms(values, ref) -> float | None:
    try:
        parts = [_ratio(v) for v in values]
    except TypeError:
        return None
    if len(parts) != 3 or any(p is None for p in parts):
        return None
    deg = parts[0] + parts[1] / 60 + parts[2] / 3600
    if _text(ref).upper() in ("S", "W"):
        deg = -deg
    return round(deg, 6)


def _valid_coords(lat, lon) -> bool:
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0)


def image_meta(data: bytes) -> dict:
    Image, _ = _pil()
    out: dict = {"kind": "photo"}
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if width * height > MAX_PIXELS:
                raise ValueError("image too large")
            exif = img.getexif()
    ifd0 = dict(exif)
    try:
        sub = dict(exif.get_ifd(0x8769))  # Exif IFD
    except Exception:
        sub = {}
    try:
        gps = dict(exif.get_ifd(0x8825))  # GPS IFD
    except Exception:
        gps = {}
    orientation = ifd0.get(0x0112)
    if orientation in (5, 6, 7, 8):  # повернуто на 90° — ширина й висота міняються
        width, height = height, width
    out.update(width=int(width), height=int(height))
    taken = _text(sub.get(0x9003) or sub.get(0x9004) or ifd0.get(0x0132))
    if taken:
        out["taken_at"] = taken
        offset = _text(sub.get(0x9011) or sub.get(0x9010))
        if offset:
            out["offset"] = offset
    for key, tag in (("make", 0x010F), ("model", 0x0110)):
        value = _text(ifd0.get(tag))
        if value:
            out[key] = value
    lens = _text(sub.get(0xA434))
    if lens:
        out["lens"] = lens
    lat = _dms(gps.get(2) or (), gps.get(1))
    lon = _dms(gps.get(4) or (), gps.get(3))
    if _valid_coords(lat, lon):
        out["lat"], out["lon"] = lat, lon
        alt = _ratio(gps.get(6))
        if alt is not None and abs(alt) < 100000:
            out["alt"] = round(-alt if gps.get(5) in (1, b"\x01") else alt, 1)
    return out


def parse_iso6709(value: str):
    """«+50.4501+030.5234+167.000/» → (50.4501, 30.5234)."""
    import re

    m = re.match(r"^\s*([+-]\d{1,2}(?:\.\d+)?)([+-]\d{1,3}(?:\.\d+)?)", value or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return (round(lat, 6), round(lon, 6)) if _valid_coords(lat, lon) else None


def video_meta_file(path: str) -> dict:
    import json

    cmd = [
        "ffprobe", "-v", "error", "-hide_banner",
        "-protocol_whitelist", "file", "-format_whitelist", VIDEO_FORMATS,
        "-print_format", "json", "-show_format", "-show_streams", path,
    ]  # fmt: skip
    res = subprocess.run(cmd, check=False, timeout=FFPROBE_TIMEOUT, capture_output=True)
    if res.returncode != 0:
        raise ValueError("ffprobe failed")
    info = json.loads(res.stdout[:2_000_000] or b"{}")
    fmt = info.get("format") or {}
    tags = {str(k).lower(): v for k, v in (fmt.get("tags") or {}).items()}
    out: dict = {"kind": "video"}
    try:
        duration = float(fmt.get("duration"))
        if 0 < duration < 7 * 24 * 3600:
            out["duration"] = round(duration, 2)
    except (TypeError, ValueError):
        pass
    for stream in info.get("streams") or []:
        if stream.get("codec_type") != "video":
            continue
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
        rotation = 0
        for side in stream.get("side_data_list") or []:
            try:
                rotation = int(float(side.get("rotation", 0)))
            except (TypeError, ValueError):
                pass
        try:
            rotation = rotation or int((stream.get("tags") or {}).get("rotate", 0))
        except (TypeError, ValueError):
            pass
        if abs(rotation) % 180 == 90:
            width, height = height, width
        if width and height:
            out.update(width=width, height=height)
        break
    # Apple: локальний час зі зміщенням; інші — creation_time в UTC
    taken = _text(tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time") or tags.get("date"))
    if taken:
        out["taken_at"] = taken
    make = _text(tags.get("com.apple.quicktime.make") or tags.get("make"))
    model = _text(tags.get("com.apple.quicktime.model") or tags.get("model"))
    if make:
        out["make"] = make
    if model:
        out["model"] = model
    coords = parse_iso6709(_text(tags.get("com.apple.quicktime.location.iso6709") or tags.get("location") or ""))
    if coords:
        out["lat"], out["lon"] = coords
    return out


def video_meta(data: bytes) -> dict:
    with tempfile.TemporaryDirectory(prefix="bc-video-") as tmp:
        src = os.path.join(tmp, "in.video")
        fd = os.open(src, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return video_meta_file(src)


def extract_meta(kind: str, data: bytes) -> dict:
    if len(data) > (MAX_VIDEO_INPUT_BYTES if kind == "video" else MAX_INPUT_BYTES):
        raise ValueError("input too large")
    if kind == "image":
        return image_meta(data)
    if kind == "video":
        return video_meta(data)
    raise ValueError("unknown kind")
