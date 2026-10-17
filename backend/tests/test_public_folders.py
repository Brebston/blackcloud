"""Публічні посилання на теки й «скринька для файлів»."""

import io
import zipfile
from datetime import timedelta

import pytest
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.storage.models import File, Folder, PublicLink

from .test_photos import jpeg
from .test_storage import body, upload

LINKS = "/api/files/links/"


def _token(url):
    return url.split("#", 1)[1]


@pytest.fixture
def trip(alice, client_for):
    """«Норвегія 2026»: фото в корені, підтека «День 1» з текстом; поруч — приватна тека."""
    c = client_for(alice)
    root = Folder.objects.create(owner=alice, name="Норвегія 2026")
    day = Folder.objects.create(owner=alice, name="День 1", parent=root)
    private = Folder.objects.create(owner=alice, name="Приватне")
    photo = upload(c, jpeg(), "fjord.jpg", folder=str(root.pk))["id"]
    note = upload(c, b"hello norway", "note.txt", folder=str(day.pk))["id"]
    secret = upload(c, b"secret", "secret.txt", folder=str(private.pk))["id"]
    return c, root, day, private, photo, note, secret


def _open(kind_url, password=None):
    anon = APIClient()
    token = _token(kind_url)
    info = anon.post("/api/public/info/", {"token": token}, format="json").json()
    r = anon.post("/api/public/authorize/", {"token": token, **({"password": password} if password else {})}, format="json")
    return anon, info, r


@pytest.mark.django_db
def test_folder_link_browse_and_download(trip):
    c, root, day, private, photo, note, secret = trip
    r = c.post(LINKS, {"kind": "folder", "folder": str(root.pk), "password": "fjords-2026", "expires_days": 3}, format="json")
    assert r.status_code == 201, r.content
    assert r.json()["kind"] == "folder" and r.json()["file_name"] == "Норвегія 2026"
    anon, info, auth = _open(r.json()["url"], "wrong-password")
    assert info["kind"] == "folder" and info["requires_password"] and "size" not in info
    assert auth.status_code == 403
    anon, _info, auth = _open(r.json()["url"], "fjords-2026")
    grant = auth.json()["grant"]
    listing = anon.post("/api/public/folder/list/", {"grant": grant}, format="json").json()
    assert [f["name"] for f in listing["folders"]] == ["День 1"]
    assert [f["name"] for f in listing["files"]] == ["fjord.jpg"]
    sub = anon.post("/api/public/folder/list/", {"grant": grant, "folder": str(day.pk)}, format="json").json()
    assert [b["name"] for b in sub["breadcrumbs"]] == ["Норвегія 2026", "День 1"]
    # Поза текою посилання — нічого
    assert anon.post("/api/public/folder/list/", {"grant": grant, "folder": str(private.pk)}, format="json").status_code == 404
    assert anon.post("/api/public/folder/file/", {"grant": grant, "file": secret}, format="json").status_code == 404
    # Без пропуску — ні
    assert APIClient().post("/api/public/folder/list/", {"grant": "nope"}, format="json").status_code == 403
    # Завантаження фото — без геолокації
    dl = anon.post("/api/public/folder/file/", {"grant": grant, "file": photo}, format="json").json()["download_url"]
    data = body(anon.get(dl))
    with Image.open(io.BytesIO(data)) as img:
        assert dict(img.getexif().get_ifd(0x8825)) == {}
    assert anon.get(dl).status_code == 403  # квиток одноразовий
    # ZIP усієї теки — теж без геолокації
    z = anon.post("/api/public/folder/zip/", {"grant": grant}, format="json").json()
    assert z["files"] == 2
    archive = zipfile.ZipFile(io.BytesIO(body(anon.get(z["download_url"]))))
    names = sorted(archive.namelist())
    assert names == ["Норвегія 2026/", "Норвегія 2026/fjord.jpg", "Норвегія 2026/День 1/", "Норвегія 2026/День 1/note.txt"]
    with Image.open(io.BytesIO(archive.read("Норвегія 2026/fjord.jpg"))) as img:
        assert dict(img.getexif().get_ifd(0x8825)) == {}
    assert archive.read("Норвегія 2026/День 1/note.txt") == b"hello norway"
    assert "secret.txt" not in " ".join(names)
    # Відкликали — пропуск більше не діє
    PublicLink.objects.update(revoked_at=timezone.now())
    assert anon.post("/api/public/folder/list/", {"grant": grant}, format="json").status_code == 404


@pytest.mark.django_db
def test_folder_link_trashed_or_expired(trip):
    c, root, *_rest = trip
    r = c.post(LINKS, {"kind": "folder", "folder": str(root.pk)}, format="json").json()
    anon, _info, auth = _open(r["url"])
    grant = auth.json()["grant"]
    Folder.objects.filter(pk=root.pk).update(deleted_at=timezone.now())
    assert anon.post("/api/public/folder/list/", {"grant": grant}, format="json").status_code == 404
    Folder.objects.filter(pk=root.pk).update(deleted_at=None)
    PublicLink.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    assert anon.post("/api/public/info/", {"token": _token(r["url"])}, format="json").status_code == 404


@pytest.mark.django_db
def test_cannot_link_foreign_folder(trip, make_user, client_for):
    _c, root, *_rest = trip
    bob = client_for(make_user("bob"))
    assert bob.post(LINKS, {"kind": "folder", "folder": str(root.pk)}, format="json").status_code == 404
    assert bob.post(LINKS, {"kind": "drop"}, format="json").status_code == 400


def _drop_upload(anon, grant, name, data, sender="Олена Коваль"):
    h = {"HTTP_X_LINK_GRANT": grant}
    r = anon.post("/api/public/drop/start/", {"grant": grant, "name": name, "size": len(data), "sender": sender}, format="json")
    if r.status_code != 201:
        return r
    info = r.json()
    cs = info["chunk_size"]
    for i in range(info["chunks_total"]):
        rr = anon.put(f"/api/public/drop/chunk/{i}/", data[i * cs : (i + 1) * cs], content_type="application/octet-stream", HTTP_X_UPLOAD_TOKEN=info["upload"], **h)
        assert rr.status_code == 200, rr.content
    return anon.post("/api/public/drop/complete/", {"grant": grant, "upload": info["upload"]}, format="json")


@pytest.mark.django_db
def test_drop_box(trip, alice, settings):
    settings.UPLOAD_CHUNK_SIZE = 1024
    c, root, *_rest = trip
    r = c.post(LINKS, {"kind": "drop", "folder": str(root.pk), "note": "Скиньте фото з поїздки", "max_upload_mb": 1}, format="json").json()
    anon, info, auth = _open(r["url"])
    assert info["kind"] == "drop" and info["note"] == "Скиньте фото з поїздки" and info["owner_name"]
    grant = auth.json()["grant"]
    # Вмісту теки гість не бачить
    assert anon.post("/api/public/folder/list/", {"grant": grant}, format="json").status_code == 404
    done = _drop_upload(anon, grant, "IMG_1.jpg", b"x" * 3000)
    assert done.status_code == 200, done.content
    _drop_upload(anon, grant, "IMG_1.jpg", b"y" * 10)  # те саме ім'я — не перезаписує
    sender_folder = Folder.objects.get(owner=alice, parent=root, name="Олена Коваль")
    names = sorted(File.objects.filter(folder=sender_folder).values_list("name", flat=True))
    assert names == ["IMG_1 (1).jpg", "IMG_1.jpg"]
    link = PublicLink.objects.get()
    assert link.upload_count == 2 and link.upload_bytes == 3010
    from apps.core.models import Notification

    assert Notification.objects.filter(user=alice, title__contains="Олена Коваль").count() == 1
    # Ім'я обов'язкове; ліміт скриньки
    assert _drop_upload(anon, grant, "a.jpg", b"z", sender=" ").status_code == 400
    assert _drop_upload(anon, grant, "big.bin", b"z" * (1024 * 1024)).status_code == 403
    # Квота власника
    alice.refresh_from_db()
    assert alice.used_bytes >= 3010


@pytest.mark.django_db
def test_file_links_still_work(alice, client_for):
    c = client_for(alice)
    info = upload(c, b"public bytes")
    url = c.post(LINKS, {"file": info["id"]}, format="json").json()["url"]
    anon, meta, auth = _open(url)
    assert meta["kind"] == "file" and meta["size"] == 12
    assert body(anon.get(auth.json()["download_url"])) == b"public bytes"
