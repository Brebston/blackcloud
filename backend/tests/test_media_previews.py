"""Перегляд фото й відео: велике зображення для HEIC/TIFF, кадр із відео, Range для відео."""

import io
import os
import shutil
import subprocess
import tempfile

import pytest
from rest_framework.test import APIClient

from apps.storage import thumbnails, thumbrender
from apps.storage.models import File
from apps.storage.objectstore import get_store

from .test_storage import body, upload

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def image_bytes(fmt: str, size=(4000, 3000)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (20, 120, 200)).save(buf, format=fmt)
    return buf.getvalue()


def make_video(seconds: int = 2) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "v.mp4")
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=10:duration={seconds}",
             "-pix_fmt", "yuv420p", "-y", out],
            check=True, timeout=60,
        )  # fmt: skip
        with open(out, "rb") as fh:
            return fh.read()


# ─────────────────────────── Рендер ───────────────────────────


def test_preview_is_large_and_thumbnail_small():
    from PIL import Image

    data = image_bytes("JPEG")
    thumb = Image.open(io.BytesIO(thumbrender.render("image", data)))
    preview = Image.open(io.BytesIO(thumbrender.render("preview", data)))
    assert max(thumb.size) == thumbrender.THUMB_SIZE
    assert max(preview.size) == thumbrender.PREVIEW_SIZE
    assert preview.size[0] / preview.size[1] == pytest.approx(4 / 3, rel=0.01)  # пропорції збережено


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg не встановлено")
def test_video_frame_rendered():
    from PIL import Image

    out = thumbrender.render("video", make_video())
    img = Image.open(io.BytesIO(out))
    assert img.format == "WEBP" and max(img.size) <= thumbrender.THUMB_SIZE


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg не встановлено")
def test_video_render_refuses_playlists_and_local_files():
    """Плейлист HLS у «відео» не повинен змусити ffmpeg читати локальні файли чи мережу."""
    playlist = b"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:10.0,\nfile:///etc/passwd\n#EXT-X-ENDLIST\n"
    with pytest.raises(Exception):
        thumbrender.render("video", playlist)


# ─────────────────────────── Збереження й API ───────────────────────────


@pytest.mark.django_db
def test_tiff_gets_encrypted_preview_and_jpeg_does_not(alice, bob, client_for):
    from PIL import Image

    c = client_for(alice)
    info = upload(c, image_bytes("TIFF", (3000, 2000)), name="scan.tiff")
    f = File.objects.get(pk=info["id"])
    assert f.mime_type == "image/tiff"
    assert f.has_thumbnail and f.has_preview
    assert b"WEBP" not in get_store().get(f.preview_key())[:64]  # на диску — шифротекст

    listed = [x for x in c.get("/api/files/browse/").json()["files"] if x["id"] == info["id"]][0]
    assert listed["has_preview"] is True
    r = c.get(f"/api/files/items/{f.id}/preview-image/")
    assert r.status_code == 200 and r["Content-Type"] == "image/webp"
    assert max(Image.open(io.BytesIO(r.content)).size) <= thumbrender.PREVIEW_SIZE
    assert client_for(bob).get(f"/api/files/items/{f.id}/preview-image/").status_code == 404

    jpg = upload(c, image_bytes("JPEG", (800, 600)), name="a.jpg")
    jf = File.objects.get(pk=jpg["id"])
    assert jf.has_thumbnail and not jf.has_preview  # JPEG браузер показує сам
    assert c.get(f"/api/files/items/{jf.id}/preview-image/").status_code == 404


@pytest.mark.django_db
@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg не встановлено")
def test_video_thumbnail_generated(alice, client_for):
    info = upload(client_for(alice), make_video(), name="clip.mp4")
    f = File.objects.get(pk=info["id"])
    assert f.mime_type.startswith("video/")
    assert f.has_thumbnail and not f.has_preview


@pytest.mark.django_db
def test_failed_thumbnails_retried_by_backfill_then_given_up(alice, client_for, monkeypatch):
    from apps.storage import tasks

    calls = []

    def busy(kind, data):
        calls.append(kind)
        raise OSError("503 thumbnailer busy")

    monkeypatch.setattr(thumbnails, "render", busy)
    info = upload(client_for(alice), image_bytes("PNG", (300, 200)), name="p.png")
    f = File.objects.get(pk=info["id"])
    assert not f.has_thumbnail and len(calls) == 1

    tasks.backfill_thumbnails(limit=10)
    tasks.backfill_thumbnails(limit=10)
    assert len(calls) == 3
    tasks.backfill_thumbnails(limit=10)  # 3 невдачі — більше не пробує
    assert len(calls) == 3

    monkeypatch.undo()
    assert tasks.backfill_thumbnails(limit=10, retry_failed=True) == 1
    assert File.objects.get(pk=f.pk).has_thumbnail


@pytest.mark.django_db
def test_mov_preview_inline_with_range(alice, client_for, settings, monkeypatch):
    c = client_for(alice)
    data = bytes(range(256)) * 400  # 102 400 байт
    info = upload(c, data, name="clip.mov")
    File.objects.filter(pk=info["id"]).update(mime_type="video/quicktime")

    url = c.get(f"/api/files/items/{info['id']}/preview/").json()["url"]
    path = url.split(settings.USERCONTENT_HOST, 1)[1]
    anon = APIClient()

    r = anon.get(path, HTTP_HOST=settings.USERCONTENT_HOST)
    assert r.status_code == 200 and r["Content-Type"] == "video/quicktime"
    assert r["Accept-Ranges"] == "bytes" and body(r) == data

    r = anon.get(path, HTTP_HOST=settings.USERCONTENT_HOST, HTTP_RANGE="bytes=100-70099")
    assert r.status_code == 206
    assert r["Content-Range"] == f"bytes 100-70099/{len(data)}"
    assert body(r) == data[100:70100]

    r = anon.get(path, HTTP_HOST=settings.USERCONTENT_HOST, HTTP_RANGE="bytes=-10")
    assert r.status_code == 206 and body(r) == data[-10:]

    r = anon.get(path, HTTP_HOST=settings.USERCONTENT_HOST, HTTP_RANGE=f"bytes={len(data)}-")
    assert r.status_code == 416

    # завантаження з основного домену — як і раніше, повністю й як вкладення
    r = c.get(f"/api/files/items/{info['id']}/download/", HTTP_RANGE="bytes=0-9")
    assert r.status_code == 200 and r["Content-Disposition"].startswith("attachment")
