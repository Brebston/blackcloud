import io
import json
import shutil

import pytest
from rest_framework.test import APIClient

from apps.storage import office
from apps.storage.models import File
from apps.storage.objectstore import get_store

from .test_storage import body, upload


def png_bytes(size=(1200, 800), color=(200, 30, 30)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def minimal_pdf() -> bytes:
    # Найпростіший валідний PDF з однією сторінкою
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


# ─────────────────────────── Мініатюри ───────────────────────────


@pytest.mark.django_db
def test_image_thumbnail_generated_encrypted_and_private(alice, bob, client_for):
    from PIL import Image

    ca = client_for(alice)
    info = upload(ca, png_bytes(), name="photo.png")
    f = File.objects.get(pk=info["id"])
    assert f.has_thumbnail is True

    # на диску мініатюра лише у зашифрованому вигляді
    raw = get_store().get(f.thumb_key())
    assert b"WEBP" not in raw[:64]

    r = ca.get(f"/api/files/items/{f.id}/thumbnail/")
    assert r.status_code == 200 and r["Content-Type"] == "image/webp"
    img = Image.open(io.BytesIO(r.content))
    assert max(img.size) <= 480

    # інший користувач мініатюру не бачить
    assert client_for(bob).get(f"/api/files/items/{f.id}/thumbnail/").status_code == 404


@pytest.mark.django_db
@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="poppler не встановлено")
def test_pdf_thumbnail(alice, client_for):
    info = upload(client_for(alice), minimal_pdf(), name="doc.pdf")
    assert File.objects.get(pk=info["id"]).has_thumbnail is True


@pytest.mark.django_db
def test_garbage_image_has_no_thumbnail(alice, client_for):
    info = upload(client_for(alice), b"\x89PNG\r\n\x1a\n" + b"garbage" * 100, name="broken.png")
    f = File.objects.get(pk=info["id"])
    assert f.status == File.Status.CLEAN
    assert f.has_thumbnail is False


# ─────────────────────────── Нова версія вмісту ───────────────────────────


@pytest.mark.django_db
def test_replace_content_new_version_quota_and_cleanup(alice, client_for, settings):
    from apps.storage import services

    settings.UPLOAD_CHUNK_SIZE = 1024
    c = client_for(alice)
    info = upload(c, b"a" * 3000, name="notes.txt")
    f = File.objects.get(pk=info["id"])
    old_key = f.chunk_key(0)

    services.replace_content(f, b"b" * 500)
    f.refresh_from_db()
    alice.refresh_from_db()
    assert f.content_version == 1 and f.size == 500
    assert alice.used_bytes == 500
    assert f.status == File.Status.CLEAN  # повторна перевірка антивірусом пройшла
    assert body(c.get(f"/api/files/items/{f.id}/download/")) == b"b" * 500
    with pytest.raises(FileNotFoundError):
        get_store().get(old_key)


@pytest.mark.django_db
def test_replace_content_respects_quota(alice, client_for):
    from rest_framework.exceptions import PermissionDenied

    from apps.storage import services

    info = upload(client_for(alice), b"x" * 100, name="a.txt")
    type(alice).objects.filter(pk=alice.pk).update(quota_bytes=150)  # used_bytes=100
    with pytest.raises(PermissionDenied):
        services.replace_content(File.objects.get(pk=info["id"]), b"y" * 200)


# ─────────────────────────── ONLYOFFICE ───────────────────────────


@pytest.fixture
def office_on(settings):
    settings.OFFICE_ENABLED = True
    settings.OFFICE_JWT_SECRET = "test-office-secret"
    return settings


def test_jwt_roundtrip_and_tamper(office_on):
    token = office.jwt_encode({"a": 1})
    assert office.jwt_decode(token)["a"] == 1
    head, payload, sig = token.split(".")
    forged = office._b64(json.dumps({"a": 2}).encode())
    with pytest.raises(office.JWTError):
        office.jwt_decode(f"{head}.{forged}.{sig}")
    with pytest.raises(office.JWTError):
        office.jwt_decode("eyJhbGciOiJub25lIn0.e30.")


@pytest.mark.django_db
def test_office_disabled_returns_404(alice, client_for, settings):
    settings.OFFICE_ENABLED = False
    info = upload(client_for(alice), b"hello", name="a.docx")
    assert client_for(alice).get(f"/api/office/config/{info['id']}/").status_code == 404


@pytest.mark.django_db
def test_office_config_modes(alice, bob, client_for, office_on):
    ca = client_for(alice)
    folder = ca.post("/api/files/folders/", {"name": "Docs"}, format="json").json()
    info = upload(ca, b"PK fake docx", name="report.docx", folder=folder["id"])

    cfg = ca.get(f"/api/office/config/{info['id']}/").json()["config"]
    assert cfg["editorConfig"]["mode"] == "edit"
    assert cfg["documentType"] == "word"
    assert "callbackUrl" in cfg["editorConfig"]
    assert office.jwt_decode(cfg["token"])["document"]["key"] == cfg["document"]["key"]

    # одержувач спільного доступу — лише перегляд, без callback
    ca.post("/api/files/shares/", {"username": "bob", "folder": folder["id"]}, format="json")
    cfg_b = client_for(bob).get(f"/api/office/config/{info['id']}/").json()["config"]
    assert cfg_b["editorConfig"]["mode"] == "view"
    assert "callbackUrl" not in cfg_b["editorConfig"]

    # сторонній користувач — нічого
    from django.contrib.auth import get_user_model

    eve = get_user_model().objects.create_user("eve", "eve@example.org", "Very-Strong-Passw0rd!")
    assert client_for(eve).get(f"/api/office/config/{info['id']}/").status_code == 404


@pytest.mark.django_db
def test_office_file_endpoint_requires_valid_token(alice, client_for, office_on):
    ca = client_for(alice)
    info = upload(ca, b"docx-bytes", name="a.docx")
    cfg = ca.get(f"/api/office/config/{info['id']}/").json()["config"]
    path = cfg["document"]["url"].replace("http://backend:8000", "")
    anon = APIClient()
    assert body(anon.get(path)) == b"docx-bytes"
    assert anon.get(f"/api/office/file/{info['id']}/?t=forged").status_code == 404


@pytest.mark.django_db
def test_office_callback_saves_new_version(alice, client_for, office_on, monkeypatch):
    ca = client_for(alice)
    info = upload(ca, b"old content", name="a.docx")
    cfg = ca.get(f"/api/office/config/{info['id']}/").json()["config"]
    cb_path = cfg["editorConfig"]["callbackUrl"].replace("http://backend:8000", "")

    fetched = {}

    def fake_fetch(url):
        fetched["url"] = url
        return b"new edited content"

    monkeypatch.setattr(office, "fetch_result", fake_fetch)
    payload = {"status": 2, "key": cfg["document"]["key"], "url": "https://evil.example/office/cache/files/x/output.docx"}
    anon = APIClient()

    # без JWT — відмова
    r = anon.post(cb_path, payload, format="json")
    assert r.status_code == 403
    assert File.objects.get(pk=info["id"]).content_version == 0

    # з валідним JWT — збереження
    r = anon.post(cb_path, {"token": office.jwt_encode(payload)}, format="json")
    assert r.json() == {"error": 0}
    f = File.objects.get(pk=info["id"])
    assert f.content_version == 1
    assert body(ca.get(f"/api/files/items/{f.id}/download/")) == b"new edited content"


def test_fetch_result_ignores_foreign_host(office_on, monkeypatch):
    captured = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b"ok"

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        return Resp()

    monkeypatch.setattr(office.urllib.request, "urlopen", fake_urlopen)
    assert office.fetch_result("https://attacker.example/cache/files/1/output.docx?md5=x") == b"ok"
    assert captured["url"] == "http://onlyoffice/cache/files/1/output.docx?md5=x"
    with pytest.raises(ValueError):
        office.fetch_result("https://x/office/../../etc/passwd")
