import pytest
from django.conf import settings

from apps.storage import crypto, services
from apps.storage.models import File, PublicLink


def upload(client, data: bytes, name="test.bin", folder=None):
    r = client.post("/api/files/uploads/", {"name": name, "size": len(data), "folder": folder}, format="json")
    assert r.status_code == 201, r.content
    info = r.json()
    cs = info["chunk_size"]
    for i in range(info["chunks_total"]):
        chunk = data[i * cs : (i + 1) * cs]
        rr = client.put(
            f"/api/files/uploads/{info['id']}/chunks/{i}/", chunk, content_type="application/octet-stream"
        )
        assert rr.status_code == 200, rr.content
    r = client.post(f"/api/files/uploads/{info['id']}/complete/")
    assert r.status_code == 200, r.content
    return r.json()


def body(response) -> bytes:
    # Відповідь стрімиться асинхронним ітератором; __iter__ збирає його синхронно
    return b"".join(response) if getattr(response, "streaming", False) else response.content


@pytest.mark.django_db
def test_upload_download_roundtrip_encrypted(alice, client_for, settings):
    settings.UPLOAD_CHUNK_SIZE = 1024
    c = client_for(alice)
    data = bytes(range(256)) * 20  # 5120 байт = 5 чанків
    info = upload(c, data)
    f = File.objects.get(pk=info["id"])
    assert f.status == File.Status.CLEAN  # Celery eager + антивірус вимкнено у тестах
    assert len(f.sha256) == 64
    # На диску лише шифротекст
    raw = (settings.LOCAL_OBJECT_STORE_DIR / f.chunk_key(0)).read_bytes()
    assert data[:1024] not in raw
    r = c.get(f"/api/files/items/{f.id}/download/")
    assert r.status_code == 200
    assert body(r) == data
    assert r["Content-Type"] == "application/octet-stream"
    assert "attachment" in r["Content-Disposition"]
    alice.refresh_from_db()
    assert alice.used_bytes == len(data)


@pytest.mark.django_db
def test_chunk_tampering_detected(alice, client_for, settings):
    settings.UPLOAD_CHUNK_SIZE = 1024
    c = client_for(alice)
    info = upload(c, b"x" * 2048)
    f = File.objects.get(pk=info["id"])
    dk = crypto.unwrap_key(f.wrapped_key, f.key_version, f.id)
    blob0 = (settings.LOCAL_OBJECT_STORE_DIR / f.chunk_key(0)).read_bytes()
    # Перестановка чанків має провалити перевірку AAD
    with pytest.raises(Exception):
        crypto.decrypt_chunk(dk, f.id, 1, blob0)


@pytest.mark.django_db
def test_quota_enforced(alice, client_for):
    alice.quota_bytes = 1000
    alice.save()
    r = client_for(alice).post("/api/files/uploads/", {"name": "big.bin", "size": 1001}, format="json")
    assert r.status_code == 403


@pytest.mark.django_db
def test_chunk_size_and_order_validated(alice, client_for, settings):
    settings.UPLOAD_CHUNK_SIZE = 1024
    c = client_for(alice)
    info = c.post("/api/files/uploads/", {"name": "a.bin", "size": 2048}, format="json").json()
    bad = c.put(f"/api/files/uploads/{info['id']}/chunks/1/", b"x" * 1024, content_type="application/octet-stream")
    assert bad.status_code == 400
    short = c.put(f"/api/files/uploads/{info['id']}/chunks/0/", b"x" * 10, content_type="application/octet-stream")
    assert short.status_code == 400
    incomplete = c.post(f"/api/files/uploads/{info['id']}/complete/")
    assert incomplete.status_code == 400


@pytest.mark.django_db
def test_other_user_cannot_access(alice, bob, client_for):
    info = upload(client_for(alice), b"secret data")
    cb = client_for(bob)
    assert cb.get(f"/api/files/items/{info['id']}/download/").status_code == 404
    assert cb.get(f"/api/files/items/{info['id']}/").status_code == 404
    assert cb.delete(f"/api/files/items/{info['id']}/").status_code == 404


@pytest.mark.django_db
def test_share_grants_read_access(alice, bob, client_for):
    ca, cb = client_for(alice), client_for(bob)
    folder = ca.post("/api/files/folders/", {"name": "Docs"}, format="json").json()
    info = upload(ca, b"shared content", folder=folder["id"])
    assert ca.post("/api/files/shares/", {"username": "bob", "folder": folder["id"]}, format="json").status_code == 201
    r = cb.get(f"/api/files/browse/?folder={folder['id']}")
    assert r.status_code == 200 and r.json()["writable"] is False
    assert body(cb.get(f"/api/files/items/{info['id']}/download/")) == b"shared content"
    # але не може видаляти
    assert cb.delete(f"/api/files/items/{info['id']}/").status_code == 404


@pytest.mark.django_db
def test_trash_restore_and_purge_releases_quota(alice, client_for):
    c = client_for(alice)
    info = upload(c, b"y" * 500)
    assert c.delete(f"/api/files/items/{info['id']}/").status_code == 204
    alice.refresh_from_db()
    assert alice.used_bytes == 500  # кошик теж займає місце
    assert c.post("/api/files/trash/restore/", {"type": "file", "id": info["id"]}, format="json").status_code == 200
    c.delete(f"/api/files/items/{info['id']}/")
    assert c.post("/api/files/trash/purge/", {"type": "file", "id": info["id"]}, format="json").status_code == 204
    alice.refresh_from_db()
    assert alice.used_bytes == 0


@pytest.mark.django_db
def test_public_link_password_and_limit(alice, client_for):
    from rest_framework.test import APIClient

    c = client_for(alice)
    info = upload(c, b"public bytes")
    r = c.post(
        "/api/files/links/", {"file": info["id"], "password": "pw-123456", "max_downloads": 1}, format="json"
    )
    assert r.status_code == 201
    token = r.json()["url"].rsplit("/", 1)[1]
    assert not PublicLink.objects.filter(token_hash=token).exists()  # у БД лише хеш

    anon = APIClient()
    assert anon.get(f"/api/public/{token}/").json()["requires_password"] is True
    assert anon.post(f"/api/public/{token}/authorize/", {"password": "nope"}, format="json").status_code == 403
    url = anon.post(f"/api/public/{token}/authorize/", {"password": "pw-123456"}, format="json").json()["download_url"]
    assert body(anon.get(url)) == b"public bytes"
    # ліміт завантажень вичерпано
    assert anon.get(url).status_code == 404
    # без підпису завантаження неможливе
    assert anon.get(f"/api/public/{token}/download/").status_code in (403, 404)


@pytest.mark.parametrize(
    "bad", ["", ".", "..", "a/b", "a\\b", "x" * 256]
)
def test_clean_name_rejects(bad):
    from rest_framework.exceptions import ValidationError

    with pytest.raises(ValidationError):
        services.clean_name(bad)


def test_clean_name_strips_bidi_override():
    # "invoice‮fdp.exe" виглядає як "invoiceexe.pdf"
    assert services.clean_name("invoice‮fdp.exe") == "invoicefdp.exe"


@pytest.mark.django_db
def test_name_collision_gets_suffix(alice, client_for):
    c = client_for(alice)
    a = upload(c, b"1", name="report.pdf")
    b = upload(c, b"2", name="report.pdf")
    assert a["name"] == "report.pdf"
    assert b["name"] == "report (1).pdf"


def test_master_key_configured():
    assert crypto.current_key_version() >= 1
    assert settings.UNSCANNED_POLICY in ("block", "allow")
