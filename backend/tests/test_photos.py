"""Галерея «Фото»: стрічка за датою зйомки, EXIF, координати (зашифровані), альбоми-посилання."""

import io
from datetime import datetime, timezone as dt_timezone

import pytest
from django.utils import timezone
from PIL import Image

from apps.photos import indexing
from apps.photos.models import Album, AlbumItem, PhotoMeta
from apps.storage.models import File

from .test_storage import upload

URL = "/api/photos/"


def jpeg(taken="2019:07:01 18:30:00", offset="+03:00", gps=True, size=(40, 30)) -> bytes:
    img = Image.new("RGB", size, "blue")
    exif = Image.Exif()
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone 15 Pro"
    sub = exif.get_ifd(0x8769)
    if taken:
        sub[0x9003] = taken
    if offset:
        sub[0x9011] = offset
    sub[0xA434] = "back camera 6.86mm f/1.78"
    if gps:
        g = exif.get_ifd(0x8825)
        g[1], g[2], g[3], g[4] = "N", (50.0, 27.0, 0.36), "E", (30.0, 31.0, 24.24)
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif.tobytes())
    return out.getvalue()


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def gallery(alice, client_for):
    c = client_for(alice)
    a = upload(c, jpeg(), "trip.jpg")
    b = upload(c, png(), "IMG_20200101_101010.png")
    upload(c, b"just text", "notes.txt")
    upload(c, jpeg(), "._trip.jpg")  # службовий файл macOS — не в стрічці
    return c, a["id"], b["id"]


@pytest.mark.django_db
def test_timeline_by_date_taken(gallery, alice):
    c, jpg_id, png_id = gallery
    # Ще не в індексі (напр., старі файли до оновлення) — у стрічці одразу, за датою завантаження,
    # позначені як «обробляються»
    PhotoMeta.objects.all().delete()
    first = c.get(URL).json()
    assert first["total"] == 2 and first["processing"] == 2
    indexing.run(100)
    body = c.get(URL).json()
    assert body["processing"] == 0
    ids = [r["id"] for r in body["results"]]
    assert ids == [png_id, jpg_id]  # 2020 (з імені файлу) новіше за 2019 (EXIF)
    photo = body["results"][1]["photo"]
    assert photo["kind"] == "photo" and photo["has_location"] is True
    assert datetime.fromisoformat(photo["taken_at"].replace("Z", "+00:00")) == datetime(2019, 7, 1, 15, 30, tzinfo=dt_timezone.utc)
    assert photo["width"] == 40 and photo["height"] == 30
    assert PhotoMeta.objects.get(file_id=png_id).taken_source == "name"


@pytest.mark.django_db
def test_detail_exif_and_encrypted_location(gallery, make_user, client_for):
    c, jpg_id, _png = gallery
    indexing.run(100)
    d = c.get(f"{URL}{jpg_id}/").json()["photo"]
    assert d["camera_make"] == "Apple" and d["camera_model"] == "iPhone 15 Pro" and d["lens"].startswith("back camera")
    assert d["location"] == {"lat": pytest.approx(50.4501, abs=1e-4), "lon": pytest.approx(30.5234, abs=1e-4)}
    assert d["taken_source"] == "exif"
    row = PhotoMeta.objects.get(file_id=jpg_id)
    assert "50.45" not in row.location and row.location  # у БД — лише шифротекст
    # Чуже — не видно
    other = client_for(make_user("bob"))
    assert other.get(f"{URL}{jpg_id}/").status_code == 404
    assert other.get(URL).json()["total"] == 0


@pytest.mark.django_db
def test_cursor_pagination(alice, client_for):
    c = client_for(alice)
    for i in range(5):
        upload(c, jpeg(taken=f"2021:01:0{i + 1} 10:00:00", gps=False), f"p{i}.jpg")
    indexing.run(100)
    seen, cursor = [], ""
    while True:
        r = c.get(URL, {"limit": 2, **({"cursor": cursor} if cursor else {})}).json()
        seen += [x["photo"]["taken_at"][:10] for x in r["results"]]
        cursor = r["next_cursor"]
        if not cursor:
            break
    assert seen == ["2021-01-05", "2021-01-04", "2021-01-03", "2021-01-02", "2021-01-01"]


@pytest.mark.django_db
def test_albums_reference_files(gallery, make_user, client_for, alice):
    c, jpg_id, png_id = gallery
    r = c.post(f"{URL}albums/", {"name": " Відпустка ", "file_ids": [jpg_id]}, format="json")
    assert r.status_code == 201, r.content
    album = r.json()
    assert album["name"] == "Відпустка" and album["count"] == 1 and album["cover"]["id"] == jpg_id
    aid = album["id"]
    assert c.post(f"{URL}albums/{aid}/items/", {"file_ids": [png_id, jpg_id]}, format="json").json()["added"] == 2
    items = c.get(f"{URL}albums/{aid}/items/").json()
    assert {x["id"] for x in items["results"]} == {jpg_id, png_id} and items["total"] == 2
    # Обкладинка — лише з альбому
    assert c.patch(f"{URL}albums/{aid}/", {"cover": png_id}, format="json").json()["cover"]["id"] == png_id
    txt = File.objects.get(name="notes.txt").pk
    assert c.patch(f"{URL}albums/{aid}/", {"cover": str(txt)}, format="json").status_code == 400
    # Не фото й чужі файли не додаються
    bob = make_user("bob")
    bob_file = upload(client_for(bob), jpeg(), "bob.jpg")["id"]
    r = c.post(f"{URL}albums/{aid}/items/", {"file_ids": [str(txt), bob_file]}, format="json")
    assert r.json()["added"] == 0
    assert client_for(bob).get(f"{URL}albums/{aid}/").status_code == 404
    # Прибрати з альбому — файл лишається
    r = c.delete(f"{URL}albums/{aid}/items/", {"file_ids": [png_id]}, format="json")
    assert r.json()["removed"] == 1 and r.json()["album"]["cover"]["id"] == jpg_id
    assert File.objects.filter(pk=png_id).exists()
    # Видалити альбом — файли на місці
    assert c.delete(f"{URL}albums/{aid}/").status_code == 204
    assert not AlbumItem.objects.exists() and File.objects.filter(pk=jpg_id).exists()


@pytest.mark.django_db
def test_trashed_and_purged_files_leave_gallery(gallery, alice):
    c, jpg_id, png_id = gallery
    album = Album.objects.create(owner=alice, name="A")
    AlbumItem.objects.create(album=album, file_id=jpg_id)
    indexing.run(100)
    File.objects.filter(pk=jpg_id).update(deleted_at=timezone.now())
    assert [r["id"] for r in c.get(URL).json()["results"]] == [png_id]
    assert c.get(f"{URL}albums/{album.pk}/items/").json()["total"] == 0
    from apps.storage import services

    services.purge_file(File.objects.get(pk=jpg_id))
    assert not PhotoMeta.objects.filter(file_id=jpg_id).exists() and not AlbumItem.objects.exists()


def test_parse_taken_variants(alice):
    utc = dt_timezone.utc
    assert indexing.parse_taken({"kind": "video", "taken_at": "2024-10-14T10:00:01.000000Z"}, alice) == datetime(2024, 10, 14, 10, 0, 1, tzinfo=utc)
    assert indexing.parse_taken({"kind": "video", "taken_at": "2024-10-14T12:00:01+0200"}, alice) == datetime(2024, 10, 14, 10, 0, 1, tzinfo=utc)
    assert indexing.parse_taken({"kind": "photo", "taken_at": "2024:10:14 12:00:01", "offset": "-05:00"}, alice) == datetime(2024, 10, 14, 17, 0, 1, tzinfo=utc)
    # Без зміщення — часовий пояс власника (Europe/Kyiv, у жовтні UTC+3)
    assert indexing.parse_taken({"kind": "photo", "taken_at": "2024:10:14 12:00:01"}, alice) == datetime(2024, 10, 14, 9, 0, 1, tzinfo=utc)
    assert indexing.parse_taken({"kind": "photo", "taken_at": "0000:00:00 00:00:00"}, alice) is None
    assert indexing.parse_taken({"kind": "photo", "taken_at": "1970:01:01 00:00:00"}, alice) is None
    assert indexing.date_from_name("WhatsApp Image 2023-05-06 at 14.15.16.jpeg", alice).date().isoformat() == "2023-05-06"
    assert indexing.date_from_name("PXL_20220304_111213456.jpg", alice).hour == 11
    assert indexing.date_from_name("photo.jpg", alice) is None


test_parse_taken_variants = pytest.mark.django_db(test_parse_taken_variants)
