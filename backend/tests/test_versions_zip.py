import io
import zipfile
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.storage import services, tasks
from apps.storage.models import File, FileVersion, Folder, Share
from apps.storage.objectstore import get_store

from .test_storage import body, upload


def upload_version(client, file_id, data: bytes, expect=200):
    r = client.post("/api/files/uploads/", {"name": "ignored", "size": len(data), "replace_file_id": file_id}, format="json")
    if r.status_code != 201:
        assert r.status_code == expect, r.content
        return r
    info = r.json()
    assert info["version"] is True and info["file_id"] == str(file_id)
    cs = info["chunk_size"]
    for i in range(info["chunks_total"]):
        rr = client.put(
            f"/api/files/uploads/{info['id']}/chunks/{i}/", data[i * cs : (i + 1) * cs], content_type="application/octet-stream"
        )
        assert rr.status_code == 200, rr.content
    r = client.post(f"/api/files/uploads/{info['id']}/complete/")
    assert r.status_code == expect, r.content
    return r


def versions(client, file_id):
    r = client.get(f"/api/files/items/{file_id}/versions/")
    assert r.status_code == 200, r.content
    return r.json()["versions"]


def used(user):
    user.refresh_from_db()
    return user.used_bytes


@pytest.fixture
def small_chunks(settings):
    settings.UPLOAD_CHUNK_SIZE = 1024
    settings.FILE_MAX_VERSIONS = 20
    return settings


# ═══════════════════════════ Версії ═══════════════════════════


@pytest.mark.django_db
def test_replace_upload_creates_version(alice, client_for, small_chunks):
    c = client_for(alice)
    info = upload(c, b"a" * 3000, name="doc.txt")
    r = upload_version(c, info["id"], b"b" * 500)
    assert r.json()["id"] == info["id"] and r.json()["name"] == "doc.txt"
    assert File.objects.filter(owner=alice).count() == 1  # без дубліката «doc (1).txt»
    assert body(c.get(f"/api/files/items/{info['id']}/download/")) == b"b" * 500
    items = versions(c, info["id"])
    assert [v["is_current"] for v in items] == [True, False]
    assert items[0]["size"] == 500 and items[1]["size"] == 3000
    assert items[1]["author"] == "alice" and items[1]["status"] == "clean"
    old = c.get(f"/api/files/items/{info['id']}/versions/{items[1]['id']}/download/")
    assert old.status_code == 200 and body(old) == b"a" * 3000
    assert "attachment" in old["Content-Disposition"] and "(v0)" in old["Content-Disposition"]
    assert old["X-Content-Type-Options"] == "nosniff" and "sandbox" in old["Content-Security-Policy"]
    assert old["Content-Type"] == "application/octet-stream"
    # Обидві версії рахуються в квоті
    assert used(alice) == 3500
    # Звичайне завантаження з тим самим ім'ям поводиться як раніше («зберегти обидва»)
    dup = upload(c, b"c", name="doc.txt")
    assert dup["name"] == "doc (1).txt"


@pytest.mark.django_db
def test_office_save_creates_version(alice, client_for, small_chunks):
    c = client_for(alice)
    info = upload(c, b"x" * 100, name="a.docx")
    f = File.objects.get(pk=info["id"])
    services.replace_content(f, b"y" * 200)
    services.replace_content(f, b"z" * 300)
    f.refresh_from_db()
    assert f.content_version == 2 and f.status == File.Status.CLEAN
    items = versions(c, f.id)
    assert [v["number"] for v in items] == [2, 1, 0]
    assert used(alice) == 600


@pytest.mark.django_db
def test_quota_prunes_oldest_versions_or_fails(alice, client_for, small_chunks):
    c = client_for(alice)
    type(alice).objects.filter(pk=alice.pk).update(quota_bytes=4000)
    info = upload(c, b"1" * 3000)
    upload_version(c, info["id"], b"2" * 500)
    assert used(alice) == 3500
    # Бракує місця — найстаріша версія (3000) видаляється автоматично
    upload_version(c, info["id"], b"3" * 1000)
    assert used(alice) == 1500
    assert [v["size"] for v in versions(c, info["id"])] == [1000, 500]
    # Навіть без історії не влазить — 403, нічого не змінилось
    upload_version(c, info["id"], b"4" * 3600, expect=403)
    assert used(alice) == 1500
    assert [v["size"] for v in versions(c, info["id"])] == [1000, 500]


@pytest.mark.django_db
def test_restore_copy_on_write_only_clean(alice, client_for, small_chunks):
    c = client_for(alice)
    info = upload(c, b"old" * 1000, name="r.txt")
    upload_version(c, info["id"], b"new")
    items = versions(c, info["id"])
    old_id = items[1]["id"]

    # Заражена версія: ні відновити, ні завантажити
    FileVersion.objects.filter(pk=old_id).update(status=File.Status.INFECTED)
    r = c.post(f"/api/files/items/{info['id']}/versions/{old_id}/restore/")
    assert r.status_code == 400
    assert c.get(f"/api/files/items/{info['id']}/versions/{old_id}/download/").status_code == 403
    FileVersion.objects.filter(pk=old_id).update(status=File.Status.SCANNING)
    assert c.post(f"/api/files/items/{info['id']}/versions/{old_id}/restore/").status_code == 400

    FileVersion.objects.filter(pk=old_id).update(status=File.Status.CLEAN)
    r = c.post(f"/api/files/items/{info['id']}/versions/{old_id}/restore/")
    assert r.status_code == 200, r.content
    assert r.json()["content_version"] == 2
    assert body(c.get(f"/api/files/items/{info['id']}/download/")) == b"old" * 1000
    items = versions(c, info["id"])
    assert [v["number"] for v in items] == [2, 1, 0]  # історія лише доповнюється
    assert items[0]["status"] == "clean"  # копія пройшла антивірус заново
    assert used(alice) == 3000 + 3 + 3000
    # Новий ключ даних, а не той самий шифротекст
    f = File.objects.get(pk=info["id"])
    assert bytes(f.wrapped_key) != bytes(FileVersion.objects.get(pk=old_id).wrapped_key)
    from apps.core.models import AuditLog

    assert AuditLog.objects.filter(action="file.version_restored", target=info["id"]).exists()


@pytest.mark.django_db
def test_delete_non_current_version(alice, client_for, small_chunks):
    c = client_for(alice)
    info = upload(c, b"a" * 2048)
    upload_version(c, info["id"], b"b" * 10)
    items = versions(c, info["id"])
    v = FileVersion.objects.get(pk=items[1]["id"])
    key = v.chunk_key(0)
    get_store().get(key)
    assert c.delete(f"/api/files/items/{info['id']}/versions/{info['id']}/").status_code == 400  # поточна
    assert c.delete(f"/api/files/items/{info['id']}/versions/{v.id}/").status_code == 204
    assert used(alice) == 10
    with pytest.raises(FileNotFoundError):
        get_store().get(key)
    assert len(versions(c, info["id"])) == 1
    assert c.delete(f"/api/files/items/{info['id']}/versions/{v.id}/").status_code == 404
    from apps.core.models import AuditLog

    assert AuditLog.objects.filter(action="file.version_deleted").exists()


@pytest.mark.django_db
def test_versions_owner_only(alice, bob, client_for, small_chunks):
    ca, cb = client_for(alice), client_for(bob)
    info = upload(ca, b"a" * 100)
    upload_version(ca, info["id"], b"b" * 100)
    vid = versions(ca, info["id"])[1]["id"]
    Share.objects.create(owner=alice, recipient=bob, file_id=info["id"])
    # Отримувач спільного доступу бачить поточний файл, але не історію
    assert cb.get(f"/api/files/items/{info['id']}/download/").status_code == 200
    assert cb.get(f"/api/files/items/{info['id']}/versions/").status_code == 404
    assert cb.get(f"/api/files/items/{info['id']}/versions/{vid}/download/").status_code == 404
    assert cb.post(f"/api/files/items/{info['id']}/versions/{vid}/restore/").status_code == 404
    assert cb.delete(f"/api/files/items/{info['id']}/versions/{vid}/").status_code == 404
    # Не можна завантажити нову версію чужого файлу
    r = cb.post("/api/files/uploads/", {"name": "x", "size": 1, "replace_file_id": info["id"]}, format="json")
    assert r.status_code == 404
    # ...і дописати чанк у чужу сесію нової версії
    r = ca.post("/api/files/uploads/", {"name": "x", "size": 10, "replace_file_id": info["id"]}, format="json")
    pending = r.json()["id"]
    assert cb.put(f"/api/files/uploads/{pending}/chunks/0/", b"0" * 10, content_type="application/octet-stream").status_code == 404
    assert cb.post(f"/api/files/uploads/{pending}/complete/").status_code == 404
    # Версія іншого файлу через чужий шлях
    other = upload(ca, b"zz")
    assert ca.get(f"/api/files/items/{other['id']}/versions/{vid}/download/").status_code == 404


@pytest.mark.django_db
def test_pending_version_abort_and_stale_cleanup(alice, client_for, small_chunks):
    c = client_for(alice)
    info = upload(c, b"a" * 100)
    r = c.post("/api/files/uploads/", {"name": "x", "size": 2000, "replace_file_id": info["id"]}, format="json")
    assert used(alice) == 2100
    assert c.delete(f"/api/files/uploads/{r.json()['id']}/").status_code == 204
    assert used(alice) == 100
    r = c.post("/api/files/uploads/", {"name": "x", "size": 2000, "replace_file_id": info["id"]}, format="json")
    c.put(f"/api/files/uploads/{r.json()['id']}/chunks/0/", b"0" * 1024, content_type="application/octet-stream")
    FileVersion.objects.filter(pk=r.json()["id"]).update(upload_expires_at=timezone.now() - timedelta(minutes=1))
    tasks.cleanup_stale_uploads()
    assert used(alice) == 100 and not FileVersion.objects.exists()
    # Незавершена сесія не показується в історії
    assert len(versions(c, info["id"])) == 1


@pytest.mark.django_db
def test_prune_max_versions_and_age(alice, client_for, small_chunks):
    small_chunks.FILE_MAX_VERSIONS = 3
    c = client_for(alice)
    info = upload(c, b"0")
    f = File.objects.get(pk=info["id"])
    for i in range(1, 6):
        services.replace_content(f, str(i).encode() * (i + 1))
    items = versions(c, info["id"])
    assert [v["number"] for v in items] == [5, 4, 3]
    assert used(alice) == 6 + 5 + 4
    # Старі об'єкти прибрано
    with pytest.raises(FileNotFoundError):
        get_store().get(f.chunk_key(0, 1))

    # Пониження ліміту + вік: щоденне завдання
    small_chunks.FILE_MAX_VERSIONS = 20
    small_chunks.FILE_VERSIONS_MAX_AGE_DAYS = 30
    FileVersion.objects.filter(number=3).update(archived_at=timezone.now() - timedelta(days=31))
    tasks.prune_file_versions()
    assert [v["number"] for v in versions(c, info["id"])] == [5, 4]
    small_chunks.FILE_MAX_VERSIONS = 1
    tasks.prune_file_versions()
    assert [v["number"] for v in versions(c, info["id"])] == [5]
    assert used(alice) == 6


@pytest.mark.django_db
def test_purge_file_removes_all_versions(alice, client_for, small_chunks):
    c = client_for(alice)
    info = upload(c, b"a" * 1500)
    upload_version(c, info["id"], b"b" * 700)
    c.post("/api/files/uploads/", {"name": "x", "size": 50, "replace_file_id": info["id"]}, format="json")
    assert used(alice) == 2250
    assert c.delete(f"/api/files/items/{info['id']}/").status_code == 204
    r = c.post("/api/files/trash/purge/", {"type": "file", "id": info["id"]}, format="json")
    assert r.status_code == 204
    assert used(alice) == 0
    assert not FileVersion.objects.exists()
    assert not (small_chunks.LOCAL_OBJECT_STORE_DIR / "files" / info["id"]).exists()


@pytest.mark.django_db
def test_trashed_file_versions_hidden_and_replace_rejected(alice, client_for, small_chunks):
    c = client_for(alice)
    info = upload(c, b"a")
    c.delete(f"/api/files/items/{info['id']}/")
    assert c.get(f"/api/files/items/{info['id']}/versions/").status_code == 404
    r = c.post("/api/files/uploads/", {"name": "x", "size": 1, "replace_file_id": info["id"]}, format="json")
    assert r.status_code == 404


@pytest.mark.django_db
def test_scan_result_not_applied_to_newer_content(alice, client_for, small_chunks, monkeypatch):
    """Перевірка старого вмісту не повинна позначити чистим новий, ще не перевірений вміст."""
    c = client_for(alice)
    info = upload(c, b"a" * 10)
    f = File.objects.get(pk=info["id"])
    File.objects.filter(pk=f.pk).update(status=File.Status.SCANNING)

    def fake_scan(chunks, size):
        list(chunks)
        # Поки йшла перевірка, вміст замінили (імітація)
        File.objects.filter(pk=f.pk).update(content_version=7)
        return File.Status.CLEAN, "", "0" * 64

    monkeypatch.setattr(tasks, "_scan_chunks", fake_scan)
    tasks.scan_file(str(f.pk))
    f.refresh_from_db()
    assert f.status == File.Status.SCANNING


@pytest.mark.django_db
def test_archived_scanning_version_gets_scanned(alice, client_for, small_chunks):
    c = client_for(alice)
    info = upload(c, b"a" * 10)
    File.objects.filter(pk=info["id"]).update(status=File.Status.SCANNING)
    upload_version(c, info["id"], b"b" * 10)
    old = FileVersion.objects.get(file_id=info["id"])
    assert old.status == File.Status.CLEAN  # scan_version (eager) перевірив архівну версію


# ═══════════════════════════ ZIP ═══════════════════════════


def make_tree(c):
    a = c.post("/api/files/folders/", {"name": "A"}, format="json").json()
    b = c.post("/api/files/folders/", {"name": "B", "parent": a["id"]}, format="json").json()
    c.post("/api/files/folders/", {"name": "Empty", "parent": a["id"]}, format="json")
    d = c.post("/api/files/folders/", {"name": "Trashed", "parent": a["id"]}, format="json").json()
    x = upload(c, b"hello" * 1000, name="x.txt", folder=a["id"])
    y = upload(c, bytes(range(256)) * 9, name="y.bin", folder=b["id"])
    upload(c, b"secret", name="in-trashed.txt", folder=d["id"])
    z = upload(c, b"gone", name="z.txt", folder=a["id"])
    c.delete(f"/api/files/items/{z['id']}/")
    c.delete(f"/api/files/folders/{d['id']}/")
    return a, b, x, y


def open_zip(response) -> zipfile.ZipFile:
    assert response.status_code == 200, getattr(response, "content", b"")
    assert response.streaming
    return zipfile.ZipFile(io.BytesIO(body(response)))


@pytest.mark.django_db
def test_folder_zip_structure_and_roundtrip(alice, client_for, small_chunks):
    c = client_for(alice)
    a, b, x, y = make_tree(c)
    r = c.get(f"/api/files/folders/{a['id']}/zip/")
    assert r["Content-Type"] == "application/zip"
    assert r["Content-Disposition"].startswith("attachment;") and "A.zip" in r["Content-Disposition"]
    assert r["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in r["Content-Security-Policy"]
    assert "no-store" in r["Cache-Control"]
    assert r.is_async  # стрімиться асинхронним ітератором (без буферизації під ASGI)
    zf = open_zip(r)
    assert zf.testzip() is None
    names = sorted(zf.namelist())
    assert names == ["A/", "A/B/", "A/B/y.bin", "A/Empty/", "A/x.txt"]
    assert zf.read("A/x.txt") == b"hello" * 1000
    assert zf.read("A/B/y.bin") == bytes(range(256)) * 9
    from apps.core.models import AuditLog

    assert AuditLog.objects.filter(action="files.zip_downloaded", target=a["id"]).exists()


@pytest.mark.django_db
def test_zip_skips_unclean_files(alice, client_for, small_chunks):
    c = client_for(alice)
    a = c.post("/api/files/folders/", {"name": "A"}, format="json").json()
    upload(c, b"ok", name="good.txt", folder=a["id"])
    bad = upload(c, b"evil", name="bad.exe", folder=a["id"])
    File.objects.filter(pk=bad["id"]).update(status=File.Status.INFECTED)
    zf = open_zip(c.get(f"/api/files/folders/{a['id']}/zip/"))
    assert "A/bad.exe" not in zf.namelist()
    assert zf.read("A/good.txt") == b"ok"
    skipped = zf.read("_skipped.txt").decode()
    assert "A/bad.exe" in skipped and "infected" in skipped


@pytest.mark.django_db
def test_zip_names_are_sanitized_and_deduped(alice, client_for, small_chunks):
    c = client_for(alice)
    a = c.post("/api/files/folders/", {"name": "A"}, format="json").json()
    f1 = upload(c, b"1", name="one.txt", folder=a["id"])
    f2 = upload(c, b"2", name="two.txt", folder=a["id"])
    f3 = upload(c, b"3", name="three.txt", folder=a["id"])
    f4 = upload(c, b"4", name="four.txt", folder=a["id"])
    # Імена в обхід clean_name (наприклад, давні записи): шлях, «..», дублікат
    File.objects.filter(pk=f1["id"]).update(name="../../etc/passwd")
    File.objects.filter(pk=f2["id"]).update(name="..")
    File.objects.filter(pk=f3["id"]).update(name="Same.txt")
    File.objects.filter(pk=f4["id"]).update(name="same.txt")
    Folder.objects.filter(pk=a["id"]).update(name="/abs‮")
    zf = open_zip(c.get(f"/api/files/folders/{a['id']}/zip/"))
    names = zf.namelist()
    for n in names:
        assert not n.startswith("/") and ".." not in n.split("/") and "\\" not in n and "‮" not in n
    assert "_abs/.._.._etc_passwd" in names
    assert "_abs/_" in names
    assert "_abs/Same.txt" in names and "_abs/same (1).txt" in names


@pytest.mark.django_db
def test_zip_limits(alice, client_for, small_chunks):
    c = client_for(alice)
    a = c.post("/api/files/folders/", {"name": "A"}, format="json").json()
    upload(c, b"x" * 600, name="1", folder=a["id"])
    upload(c, b"x" * 600, name="2", folder=a["id"])
    small_chunks.ZIP_MAX_BYTES = 1000
    r = c.get(f"/api/files/folders/{a['id']}/zip/")
    assert r.status_code == 400 and not r.streaming
    small_chunks.ZIP_MAX_BYTES = 10**9
    small_chunks.ZIP_MAX_FILES = 2
    r = c.get(f"/api/files/folders/{a['id']}/zip/")
    assert r.status_code == 400 and "2" in r.json()["detail"]
    small_chunks.ZIP_MAX_FILES = 3
    assert c.get(f"/api/files/folders/{a['id']}/zip/").status_code == 200
    check = c.get(f"/api/files/folders/{a['id']}/zip/?check=1")
    assert check.status_code == 200 and check.json() == {"files": 2, "bytes": 1200, "skipped": 0}
    small_chunks.ZIP_MAX_FILES = 2
    assert c.get(f"/api/files/folders/{a['id']}/zip/?check=1").status_code == 400


@pytest.mark.django_db
def test_zip_permissions_and_shared_folder(alice, bob, client_for, small_chunks):
    ca, cb = client_for(alice), client_for(bob)
    a, b, x, y = make_tree(ca)
    assert cb.get(f"/api/files/folders/{a['id']}/zip/").status_code == 404
    assert cb.get(f"/api/files/zip/?file={x['id']}").status_code == 404
    Share.objects.create(owner=alice, recipient=bob, folder_id=b["id"])
    zf = open_zip(cb.get(f"/api/files/folders/{b['id']}/zip/"))
    assert zf.namelist() == ["B/", "B/y.bin"]
    assert cb.get(f"/api/files/folders/{a['id']}/zip/").status_code == 404  # лише підпапка спільна
    # Папка в кошику — 404 і для власника
    ca.delete(f"/api/files/folders/{a['id']}/")
    assert ca.get(f"/api/files/folders/{a['id']}/zip/").status_code == 404
    assert cb.get(f"/api/files/folders/{b['id']}/zip/").status_code == 404


@pytest.mark.django_db
def test_selection_zip(alice, client_for, small_chunks):
    c = client_for(alice)
    a, b, x, y = make_tree(c)
    root = upload(c, b"root", name="x.txt")
    r = c.get(f"/api/files/zip/?folder={b['id']}&file={x['id']}&file={root['id']}")
    zf = open_zip(r)
    assert "files.zip" in r["Content-Disposition"]
    assert sorted(zf.namelist()) == ["B/", "B/y.bin", "x (1).txt", "x.txt"]
    assert {zf.read("x.txt"), zf.read("x (1).txt")} == {b"hello" * 1000, b"root"}
    assert c.get("/api/files/zip/").status_code == 400
    assert c.get("/api/files/zip/?file=not-a-uuid").status_code == 404


@pytest.mark.django_db
def test_zip_streaming_is_incremental(alice, client_for, small_chunks):
    """Архів віддається частинами (по чанку), а не одним шматком у пам'яті."""
    from asgiref.sync import async_to_sync

    c = client_for(alice)
    a = c.post("/api/files/folders/", {"name": "A"}, format="json").json()
    upload(c, b"q" * 5000, name="big.bin", folder=a["id"])
    r = c.get(f"/api/files/folders/{a['id']}/zip/")

    async def collect():
        return [part async for part in r.streaming_content]

    parts = async_to_sync(collect)()
    assert len(parts) >= 5
    assert zipfile.ZipFile(io.BytesIO(b"".join(parts))).read("A/big.bin") == b"q" * 5000
