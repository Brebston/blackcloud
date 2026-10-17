"""Обране, теги, «Нещодавні»."""

import pytest
from django.utils import timezone

from apps.storage.models import File, Folder, Share, Tag

from .test_storage import upload

API = "/api/files/"


@pytest.mark.django_db
def test_stars(alice, make_user, client_for):
    c = client_for(alice)
    folder = Folder.objects.create(owner=alice, name="Важливе")
    f = upload(c, b"doc", "contract.pdf")["id"]
    assert c.post(f"{API}star/", {"file": f, "starred": True}, format="json").status_code == 200
    c.post(f"{API}star/", {"folder": str(folder.pk), "starred": True}, format="json")
    c.post(f"{API}star/", {"file": f, "starred": True}, format="json")  # повторно — без дубля
    data = c.get(f"{API}starred/").json()
    assert [x["name"] for x in data["folders"]] == ["Важливе"] and [x["name"] for x in data["files"]] == ["contract.pdf"]
    assert data["files"][0]["starred"] is True
    browse = c.get(f"{API}browse/").json()
    assert next(x for x in browse["files"] if x["id"] == f)["starred"] is True
    # Зірочки особисті; чужий файл без доступу — ні
    bob = make_user("bob")
    assert client_for(bob).post(f"{API}star/", {"file": f}, format="json").status_code == 404
    assert client_for(bob).get(f"{API}starred/").json()["files"] == []
    # У кошику — зникає з обраного
    File.objects.filter(pk=f).update(deleted_at=timezone.now())
    assert c.get(f"{API}starred/").json()["files"] == []
    c.post(f"{API}star/", {"folder": str(folder.pk), "starred": False}, format="json")
    assert c.get(f"{API}starred/").json()["folders"] == []


@pytest.mark.django_db
def test_tags(alice, make_user, client_for):
    c = client_for(alice)
    a = upload(c, b"1", "a.txt")["id"]
    b = upload(c, b"2", "b.txt")["id"]
    folder = Folder.objects.create(owner=alice, name="Проєкт")
    r = c.post(f"{API}tags/", {"name": " Робота ", "color": "red"}, format="json")
    assert r.status_code == 201 and r.json()["name"] == "Робота"
    tag = r.json()["id"]
    assert c.post(f"{API}tags/", {"name": "Робота", "color": "blue"}, format="json").status_code == 400
    assert c.post(f"{API}tags/", {"name": "X", "color": "neon"}, format="json").status_code == 400
    r = c.post(f"{API}tags/assign/", {"tag": tag, "files": [a, b], "folders": [str(folder.pk)]}, format="json")
    assert r.json()["changed"] == 3
    items = c.get(f"{API}tags/{tag}/items/").json()
    assert [x["name"] for x in items["files"]] == ["a.txt", "b.txt"] and items["tag"]["name"] == "Робота"
    assert items["files"][0]["tags"] == [tag]
    assert c.get(f"{API}tags/").json()[0]["count"] == 3
    c.post(f"{API}tags/assign/", {"tag": tag, "files": [a], "assign": False}, format="json")
    assert [x["name"] for x in c.get(f"{API}tags/{tag}/items/").json()["files"]] == ["b.txt"]
    assert c.patch(f"{API}tags/{tag}/", {"color": "green"}, format="json").json()["color"] == "green"
    # Чужі теги не видно, своїм не позначити чужі файли
    bob = make_user("bob")
    bc = client_for(bob)
    assert bc.get(f"{API}tags/{tag}/items/").status_code == 404
    btag = bc.post(f"{API}tags/", {"name": "Моє", "color": "blue"}, format="json").json()["id"]
    assert bc.post(f"{API}tags/assign/", {"tag": btag, "files": [b]}, format="json").status_code == 404
    # Видалення тегу — файли на місці
    assert c.delete(f"{API}tags/{tag}/").status_code == 204
    assert File.objects.filter(pk=b).exists() and not Tag.objects.filter(owner=alice).exists()


@pytest.mark.django_db
def test_recent(alice, make_user, client_for):
    c = client_for(alice)
    old = upload(c, b"old", "old.txt")["id"]
    File.objects.filter(pk=old).update(created_at=timezone.now() - timezone.timedelta(days=60))
    fresh = upload(c, b"fresh", "fresh.txt")["id"]
    recent = c.get(f"{API}recent/").json()["files"]
    assert [x["name"] for x in recent] == ["fresh.txt"] and recent[0]["recent_reason"] == "uploaded"
    # Відкрили старий файл — він нагорі
    c.get(f"{API}items/{old}/download/")
    recent = c.get(f"{API}recent/").json()["files"]
    assert [x["name"] for x in recent][:2] == ["old.txt", "fresh.txt"] and recent[0]["recent_reason"] == "opened"
    # Спільний файл, який відкривав інший користувач, — у його «Нещодавніх»
    bob = make_user("bob")
    folder = Folder.objects.create(owner=alice, name="Спільна")
    shared = upload(c, b"s", "shared.txt", folder=str(folder.pk))["id"]
    Share.objects.create(owner=alice, recipient=bob, folder=folder)
    bc = client_for(bob)
    assert bc.get(f"{API}recent/").json()["files"] == []
    bc.get(f"{API}items/{shared}/download/")
    assert [x["name"] for x in bc.get(f"{API}recent/").json()["files"]] == ["shared.txt"]
    # Доступ відкликали — зникає
    Share.objects.all().delete()
    assert bc.get(f"{API}recent/").json()["files"] == []
