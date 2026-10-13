"""Службові файли macOS (._*, .DS_Store): невидимі всюди, крім WebDAV; супутник ._X
супроводжує свій файл X; осиротілі супутники прибирає періодичне завдання."""

import io
import zipfile
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.storage import tasks
from apps.storage.models import File
from tests.test_storage import body, upload


def names(items):
    return sorted(i["name"] for i in items)


@pytest.fixture
def c(alice, client_for):
    return client_for(alice)


@pytest.fixture
def photo(c):
    """IMG.HEIC + його AppleDouble-супутник + .DS_Store у корені."""
    info = upload(c, b"photo", name="IMG.HEIC")
    upload(c, b"meta", name="._IMG.HEIC")
    upload(c, b"ds", name=".DS_Store")
    return info


def live(alice, name):
    return File.objects.filter(owner=alice, name=name, deleted_at__isnull=True).first()


@pytest.mark.django_db
def test_hidden_in_browse_search_trash_and_zip(alice, c, photo):
    assert names(c.get("/api/files/browse/").json()["files"]) == ["IMG.HEIC"]
    assert names(c.get("/api/files/search/?q=IMG").json()["files"]) == ["IMG.HEIC"]
    assert c.get("/api/files/search/?q=DS_Store").json()["files"] == []

    folder = c.post("/api/files/folders/", {"name": "A"}, format="json").json()
    upload(c, b"x", name="a.jpg", folder=folder["id"])
    upload(c, b"m", name="._a.jpg", folder=folder["id"])
    zf = zipfile.ZipFile(io.BytesIO(body(c.get(f"/api/files/folders/{folder['id']}/zip/"))))
    assert sorted(n.split("/")[-1] for n in zf.namelist() if not n.endswith("/")) == ["a.jpg"]

    assert c.delete(f"/api/files/items/{photo['id']}/").status_code == 204
    trash = c.get("/api/files/trash/").json()
    assert names(trash["files"]) == ["IMG.HEIC"]  # супутник у кошику, але невидимий


@pytest.mark.django_db
def test_companion_follows_delete_restore_and_purge(alice, c, photo):
    assert c.delete(f"/api/files/items/{photo['id']}/").status_code == 204
    meta = File.objects.get(owner=alice, name="._IMG.HEIC")
    assert meta.deleted_at == File.objects.get(pk=photo["id"]).deleted_at

    r = c.post("/api/files/trash/restore/", {"type": "file", "id": photo["id"]}, format="json")
    assert r.status_code == 200
    assert live(alice, "._IMG.HEIC") is not None
    assert live(alice, ".DS_Store") is not None  # .DS_Store не чіпаємо

    c.delete(f"/api/files/items/{photo['id']}/")
    assert c.post("/api/files/trash/purge/", {"type": "file", "id": photo["id"]}, format="json").status_code == 204
    assert not File.objects.filter(owner=alice, name__in=["IMG.HEIC", "._IMG.HEIC"]).exists()
    alice.refresh_from_db()
    assert alice.used_bytes == len(b"ds")


@pytest.mark.django_db
def test_companion_follows_rename_and_move(alice, c, photo):
    folder = c.post("/api/files/folders/", {"name": "Trip"}, format="json").json()
    r = c.patch(f"/api/files/items/{photo['id']}/", {"name": "Fjord.HEIC", "target": folder["id"]}, format="json")
    assert r.status_code == 200, r.content
    meta = live(alice, "._Fjord.HEIC")
    assert meta is not None and str(meta.folder_id) == folder["id"]
    assert live(alice, "._IMG.HEIC") is None


@pytest.mark.django_db
def test_cleanup_orphans(alice, c, photo):
    upload(c, b"o", name="._gone.jpg")  # супутник без файлу
    upload(c, b"t", name="trashed.jpg")
    upload(c, b"tm", name="._trashed.jpg")
    upload(c, b"n", name="._fresh.jpg")  # свіжий — Finder ще копіює
    trashed = File.objects.get(owner=alice, name="trashed.jpg")
    trashed.deleted_at = timezone.now()
    trashed.save(update_fields=["deleted_at"])
    old = timezone.now() - timedelta(hours=2)
    File.objects.filter(owner=alice).exclude(name="._fresh.jpg").update(created_at=old)

    tasks.cleanup_macos_metadata()

    assert not File.objects.filter(name="._gone.jpg").exists()  # осиротілий — видалено остаточно
    assert File.objects.get(name="._trashed.jpg").deleted_at == trashed.deleted_at  # у кошик разом із файлом
    assert live(alice, "._IMG.HEIC") is not None  # файл поруч є — лишається
    assert live(alice, "._fresh.jpg") is not None
    assert live(alice, ".DS_Store") is not None
