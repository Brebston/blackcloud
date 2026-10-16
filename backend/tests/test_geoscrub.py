"""Публічні посилання: фото й відео віддаються без геолокації (розмір і решта метаданих ті самі)."""

import io
import os
import random
import shutil
import subprocess
import tempfile

import pytest
from PIL import Image
from rest_framework.test import APIClient

from apps.storage import geoscrub

from .test_photos import jpeg
from .test_storage import body, upload


def gps_of(data: bytes) -> dict:
    with Image.open(io.BytesIO(data)) as img:
        img.load()  # зображення ціле
        return dict(img.getexif().get_ifd(0x8825))


def exif_of(data: bytes):
    with Image.open(io.BytesIO(data)) as img:
        return img.getexif()


def resave(data: bytes, fmt: str, **kw) -> bytes:
    img = Image.open(io.BytesIO(data))
    out = io.BytesIO()
    img.save(out, fmt, exif=img.getexif().tobytes(), **kw)
    return out.getvalue()


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP", "TIFF"])
def test_exif_gps_wiped_rest_kept(fmt):
    src = jpeg() if fmt == "JPEG" else resave(jpeg(), fmt)
    assert gps_of(src)  # координати є
    out = bytes(geoscrub.scrub(bytearray(src)))
    assert len(out) == len(src)
    assert gps_of(out) == {}
    exif = exif_of(out)
    assert exif[0x010F] == "Apple" and exif.get_ifd(0x8769)[0x9003] == "2019:07:01 18:30:00"
    # Ідемпотентно
    assert bytes(geoscrub.scrub(bytearray(out))) == out


def test_heic():
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    src = resave(jpeg(), "HEIF")
    assert gps_of(src)
    out = bytes(geoscrub.scrub(bytearray(src)))
    assert len(out) == len(src) and gps_of(out) == {}
    assert exif_of(out)[0x0110] == "iPhone 15 Pro"


def test_xmp_and_iso6709_and_loci():
    xmp = b'<x:xmpmeta><rdf:Description exif:GPSLatitude="50,27.006N" exif:GPSLongitude="30,31.404E" exif:DateTimeOriginal="2019"/>' \
          b"<exif:GPSAltitude>167/1</exif:GPSAltitude></x:xmpmeta>"
    out = bytes(geoscrub.scrub(bytearray(xmp)))
    assert len(out) == len(xmp) and b"50,27" not in out and b"30,31" not in out and b"167/1" not in out
    assert b'exif:DateTimeOriginal="2019"' in out
    mov = b"\x00\x00\x00\x30keys....com.apple.quicktime.location.ISO6709\x00data+50.4501+030.5234+167.000/\x00tail"
    out = bytes(geoscrub.scrub(bytearray(mov)))
    assert len(out) == len(mov) and b"50.4501" not in out and out.endswith(b"tail")
    name = b"Home\x00"
    loci = b"loci" + b"\x00\x00\x00\x00" + b"\x15\xc7" + name + b"\x00" + b"\x00\x1e\x86\x00" + b"\x00\x32\x73\x00" + b"\x00\xa7\x00\x00" + b"astrbody\x00notes\x00"
    box = (4 + len(loci)).to_bytes(4, "big") + loci
    out = bytes(geoscrub.scrub(bytearray(b"xx" + box + b"yy")))
    assert len(out) == len(box) + 4 and b"\x00\x1e\x86\x00" not in out and b"Home" in out


def test_streaming_across_chunk_boundary():
    src = jpeg()
    expected = bytes(geoscrub.scrub(bytearray(src)))
    for cut in list(range(1, 400, 7)) + [len(src) // 2, len(src) - 3]:
        out = b"".join(geoscrub.scrub_chunks([src[:cut], src[cut:]]))
        assert out == expected, cut


def test_random_data_untouched():
    rnd = random.Random(7)
    data = bytearray(rnd.randbytes(300_000))
    for magic in (b"II*\x00", b"MM\x00*", b"loci"):
        for _ in range(50):
            pos = rnd.randrange(0, len(data) - 8)
            data[pos : pos + 4] = magic
    assert bytes(geoscrub.scrub(bytearray(data))) == bytes(data)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg не встановлено")
def test_video_location_wiped():
    from apps.storage import thumbrender

    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "v.mp4")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=5", "-t", "1",
             "-metadata", "location=+50.4501+030.5234/", "-y", src],
            check=True,
        )  # fmt: skip
        assert thumbrender.video_meta_file(src).get("lat") == 50.4501
        data = open(src, "rb").read()
        out = bytes(geoscrub.scrub(bytearray(data)))
        assert len(out) == len(data)
        dst = os.path.join(d, "out.mp4")
        open(dst, "wb").write(out)
        meta = thumbrender.video_meta_file(dst)
        assert "lat" not in meta and meta.get("duration")


@pytest.mark.django_db
def test_public_link_serves_without_gps_owner_keeps_it(alice, client_for):
    c = client_for(alice)
    src = jpeg()
    info = upload(c, src, "trip.jpg")
    # Власник завантажує оригінал (з координатами)
    assert gps_of(body(c.get(f"/api/files/items/{info['id']}/download/")))
    url = c.post("/api/files/links/", {"file": info["id"]}, format="json").json()["url"]
    token = url.split("#", 1)[1]
    anon = APIClient()
    dl = anon.post("/api/public/authorize/", {"token": token}, format="json").json()
    r = anon.get(dl["download_url"])
    data = body(r)
    assert int(r["Content-Length"]) == len(data) == len(src)
    assert gps_of(data) == {}
    assert exif_of(data)[0x010F] == "Apple"
