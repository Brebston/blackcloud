"""Прибирання геолокації з фото й відео, що віддаються за публічним посиланням.

Оригінал у сховищі не змінюється: координати затираються «на льоту» під час віддачі.
Затирання не змінює розміру файлу (Content-Length той самий, структура формату ціла):

* EXIF (JPEG, HEIC/HEIF, PNG eXIf, WebP, TIFF/DNG): GPS IFD стає порожнім (0 записів),
  його дані й значення обнуляються. Дата, камера, орієнтація лишаються.
* XMP: значення exif:GPSLatitude / GPSLongitude / GPSAltitude… замінюються пробілами.
* Відео: рядки ISO 6709 («+50.4501+030.5234/», Apple і Android) — пробілами; бінарний бокс 3GPP «loci» — нулями.

Потік обробляється з перекриттям між чанками (WINDOW), тож структура на межі чанків
теж затирається. Кожна операція ідемпотентна (повторний прохід нічого не змінює).
Розбір суворо перевіряє структуру TIFF, щоб випадковий збіг байтів у стиснених даних
не пошкодив зображення.
"""

import re
import struct

WINDOW = 256 * 1024
TIFF_MAGIC = (b"II*\x00", b"MM\x00*")
TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
IFD0_KNOWN = {0x010F, 0x0110, 0x0112, 0x011A, 0x011B, 0x0128, 0x0131, 0x0132, 0x8769, 0x8825}
GPS_POINTER = 0x8825
MAX_ENTRIES = 512

XMP_ATTR_RE = re.compile(rb'(exif:GPS(?:Latitude|Longitude|Altitude|AltitudeRef|DestLatitude|DestLongitude)\s*=\s*")([^"<>]{1,80})(")')
XMP_ELEM_RE = re.compile(rb"(<exif:GPS(Latitude|Longitude|Altitude|DestLatitude|DestLongitude)>)([^<]{1,80})(</exif:GPS\2>)")
ISO6709_RE = re.compile(rb"[+-]\d{2}\.\d{2,}[+-]\d{3}\.\d{2,}(?:[+-]\d+(?:\.\d+)?)?(?:CRS[A-Za-z0-9:]*)?/")


def _u16(buf, pos, le):
    return struct.unpack_from("<H" if le else ">H", buf, pos)[0]


def _u32(buf, pos, le):
    return struct.unpack_from("<I" if le else ">I", buf, pos)[0]


def _entries(buf, start, ifd_off, le):
    """Записи IFD [(tag, type, count, value_pos)] або None, якщо структура не схожа на справжню."""
    base = start + ifd_off
    if ifd_off < 8 or base + 2 > len(buf):
        return None
    n = _u16(buf, base, le)
    if not 0 < n <= MAX_ENTRIES or base + 2 + n * 12 + 4 > len(buf):
        return None
    out, last_tag = [], -1
    for i in range(n):
        p = base + 2 + i * 12
        tag, typ, count = _u16(buf, p, le), _u16(buf, p + 2, le), _u32(buf, p + 4, le)
        if typ not in TYPE_SIZE or tag < last_tag or count > 1 << 24:
            return None
        last_tag = tag
        out.append((tag, typ, count, p + 8))
    return out


def _wipe_gps_at(buf: bytearray, start: int) -> bool:
    le = buf[start : start + 2] == b"II"
    ifd0 = _entries(buf, start, _u32(buf, start + 4, le), le)
    if not ifd0 or not any(tag in IFD0_KNOWN for tag, *_r in ifd0):
        return False
    pointer = next(((typ, vp) for tag, typ, _c, vp in ifd0 if tag == GPS_POINTER and typ in (4, 13)), None)
    if pointer is None:
        return False
    gps_off = _u32(buf, pointer[1], le)
    base = start + gps_off
    if gps_off < 8 or base + 2 > len(buf):
        return False
    if _u16(buf, base, le) == 0:
        return False  # уже порожній
    gps = _entries(buf, start, gps_off, le)
    if not gps or any(tag > 0x1F for tag, *_r in gps):  # теги GPS — 0…31
        return False
    for _tag, typ, count, vp in gps:
        size = TYPE_SIZE[typ] * count
        if size > 4:
            data = start + _u32(buf, vp, le)
            if start + 8 <= data and data + size <= len(buf) and size <= 4096:
                buf[data : data + size] = bytes(size)
    n = len(gps)
    # Кількість записів = 0; записи й «наступний IFD» — нулі
    buf[base : base + 2 + n * 12 + 4] = bytes(2 + n * 12 + 4)
    return True


def _wipe_tiff_gps(buf: bytearray) -> None:
    for magic in TIFF_MAGIC:
        pos = buf.find(magic)
        while pos != -1:
            if pos + 8 <= len(buf):
                try:
                    _wipe_gps_at(buf, pos)
                except struct.error:
                    pass
            pos = buf.find(magic, pos + 1)


def _blank(m, group: int) -> bytes:
    return b" " * (m.end(group) - m.start(group))


def _wipe_xmp(buf: bytearray) -> None:
    if b"exif:GPS" not in buf:
        return
    for m in list(XMP_ATTR_RE.finditer(buf)):
        buf[m.start(2) : m.end(2)] = _blank(m, 2)
    for m in list(XMP_ELEM_RE.finditer(buf)):
        buf[m.start(3) : m.end(3)] = _blank(m, 3)


def _wipe_iso6709(buf: bytearray) -> None:
    for m in list(ISO6709_RE.finditer(buf)):
        buf[m.start() : m.end()] = b" " * (m.end() - m.start())


def _wipe_loci(buf: bytearray) -> None:
    pos = buf.find(b"loci")
    while pos != -1:
        start = pos - 4
        if start >= 0:
            size = _u32(buf, start, False)
            end = start + size
            # size(4) type(4) version/flags(4) lang(2) name\0 role(1) lon lat alt (3×4) …
            if 22 <= size <= 4096 and end <= len(buf):
                name_end = buf.find(b"\x00", pos + 10, end)
                coords = name_end + 2 if name_end != -1 else -1
                if coords != -1 and coords + 12 <= end:
                    buf[coords : coords + 12] = bytes(12)
        pos = buf.find(b"loci", pos + 4)


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_META_CHUNKS = {b"eXIf", b"iTXt", b"tEXt"}


def _fix_png_crc(buf: bytearray) -> None:
    """PNG перевіряє CRC кожного чанка: після затирання в eXIf/iTXt перераховуємо його."""
    import zlib

    if buf[:8] != PNG_SIGNATURE:
        return
    pos = 8
    while pos + 12 <= len(buf):
        length = _u32(buf, pos, False)
        ctype = bytes(buf[pos + 4 : pos + 8])
        end = pos + 8 + length
        if end + 4 > len(buf) or ctype == b"IDAT":
            break  # метадані GPS лежать до даних зображення
        if ctype in PNG_META_CHUNKS:
            crc = zlib.crc32(buf[pos + 4 : end]) & 0xFFFFFFFF
            if _u32(buf, end, False) != crc:
                struct.pack_into(">I", buf, end, crc)
        pos = end + 4


def scrub(buf: bytearray) -> bytearray:
    _wipe_tiff_gps(buf)
    _wipe_xmp(buf)
    _wipe_iso6709(buf)
    _wipe_loci(buf)
    _fix_png_crc(buf)
    return buf


def scrub_chunks(chunks):
    """Синхронний генератор: ті самі байти, що й у chunks, але без геолокації."""
    prev = None
    for chunk in chunks:
        cur = bytearray(chunk)
        if prev is None:
            prev = cur
            continue
        head = min(len(cur), WINDOW)
        joined = scrub(prev + cur[:head])
        cur[:head] = joined[len(prev) :]
        yield bytes(joined[: len(prev)])
        prev = cur
    if prev is not None:
        yield bytes(scrub(prev))


async def ascrub_chunks(chunks):
    """Асинхронний варіант для StreamingHttpResponse під ASGI."""
    prev = None
    async for chunk in chunks:
        cur = bytearray(chunk)
        if prev is None:
            prev = cur
            continue
        head = min(len(cur), WINDOW)
        joined = scrub(prev + cur[:head])
        cur[:head] = joined[len(prev) :]
        yield bytes(joined[: len(prev)])
        prev = cur
    if prev is not None:
        yield bytes(scrub(prev))
