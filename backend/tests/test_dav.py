import asyncio
import base64
import io
import urllib.parse
from xml.etree import ElementTree as ET

import pytest
from django.test import Client, RequestFactory

from apps.dav import auth, locks, paths
from apps.dav.models import DavPassword
from apps.storage.models import File, FileVersion, Folder

NS = {"D": "DAV:"}


def basic(username, password):
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


def body(response) -> bytes:
    return b"".join(response) if getattr(response, "streaming", False) else response.content


@pytest.fixture
def dav_password(alice):
    raw = auth.generate_password()
    DavPassword.objects.create(user=alice, label="Mac", password_hash=auth.hash_password(raw))
    return raw


@pytest.fixture
def dav(alice, dav_password):
    client = Client()

    def request(method, path, data=b"", *, content_type="application/octet-stream", user="alice", password=None, **extra):
        headers = {"HTTP_AUTHORIZATION": basic(user, password or dav_password)}
        headers.update(extra)
        return client.generic(method, path, data=data, content_type=content_type, secure=True, **headers)

    return request


def multistatus(response):
    assert response.status_code == 207, response.content
    return ET.fromstring(response.content)


def hrefs(root):
    return [urllib.parse.unquote(h.text) for h in root.findall("D:response/D:href", NS)]


PROPFIND_ALL = b'<?xml version="1.0"?><D:propfind xmlns:D="DAV:"><D:allprop/></D:propfind>'


# ─────────────────────────── Автентифікація ───────────────────────────


@pytest.mark.django_db
def test_options_without_auth():
    r = Client().options("/dav/", secure=True)
    assert r.status_code == 200
    assert r["DAV"] == "1, 2"
    assert "PROPFIND" in r["Allow"] and "LOCK" in r["Allow"]


@pytest.mark.django_db
def test_auth_required_and_session_not_accepted(alice, client_for, password, dav):
    r = Client().generic("PROPFIND", "/dav/", secure=True, HTTP_DEPTH="0")
    assert r.status_code == 401
    assert r["WWW-Authenticate"] == 'Basic realm="BlackCloud", charset="UTF-8"'
    # Справжня сесія (cookie) не дає доступу до WebDAV
    c = client_for(alice, mfa=True)
    r = c.generic("PROPFIND", "/dav/", secure=True, HTTP_DEPTH="0")
    assert r.status_code == 401
    # Основний пароль акаунта не приймається
    assert dav("PROPFIND", "/dav/", password=password, HTTP_DEPTH="0").status_code == 401
    assert dav("PROPFIND", "/dav/", password="wrong", HTTP_DEPTH="0").status_code == 401
    assert dav("PROPFIND", "/dav/", HTTP_DEPTH="0").status_code == 207


@pytest.mark.django_db
def test_auth_lockout_and_audit(alice, dav, settings):
    from apps.core.models import AuditLog

    for _ in range(settings.LOGIN_MAX_FAILURES):
        assert dav("PROPFIND", "/dav/", password="bad-password", HTTP_DEPTH="0").status_code == 401
    # Навіть правильний пароль не діє під час блокування
    assert dav("PROPFIND", "/dav/", HTTP_DEPTH="0").status_code == 401
    assert AuditLog.objects.filter(action="dav.auth_failed", user=alice).count() == settings.LOGIN_MAX_FAILURES
    assert AuditLog.objects.filter(action="dav.locked", user=alice).exists()


@pytest.mark.django_db
def test_first_use_audited_and_last_used(alice, dav):
    from apps.core.models import AuditLog

    assert dav("PROPFIND", "/dav/", HTTP_DEPTH="0").status_code == 207
    assert dav("PROPFIND", "/dav/", HTTP_DEPTH="0").status_code == 207
    assert AuditLog.objects.filter(action="dav.password_first_use", user=alice).count() == 1
    assert DavPassword.objects.get(user=alice).last_used_at is not None


@pytest.mark.django_db
def test_cross_site_browser_requests_rejected(dav):
    assert dav("PROPFIND", "/dav/", HTTP_DEPTH="0", HTTP_SEC_FETCH_SITE="cross-site").status_code == 403
    assert dav("PROPFIND", "/dav/", HTTP_DEPTH="0", HTTP_ORIGIN="https://evil.example").status_code == 403


# ─────────────────────────── API паролів ───────────────────────────


@pytest.mark.django_db
def test_password_api(alice, client_for, password):
    c = client_for(alice)
    assert c.post("/api/dav/passwords/", {"label": "Mac"}, format="json").status_code == 400
    assert c.post("/api/dav/passwords/", {"label": "Mac", "password": "nope"}, format="json").status_code == 400
    r = c.post("/api/dav/passwords/", {"label": "Mac", "password": password}, format="json")
    assert r.status_code == 201, r.content
    raw = r.json()["password"]
    assert len(raw) >= 30 and r.json()["username"] == "alice"
    item = DavPassword.objects.get(pk=r.json()["id"])
    assert raw not in item.password_hash
    listing = c.get("/api/dav/passwords/").json()
    assert listing["passwords"][0]["label"] == "Mac" and "password" not in listing["passwords"][0]

    dav_client = Client()
    ok = dav_client.generic("PROPFIND", "/dav/", secure=True, HTTP_DEPTH="0", HTTP_AUTHORIZATION=basic("alice", raw))
    assert ok.status_code == 207
    # Відкликання діє одразу, навіть попри кеш автентифікації
    assert c.delete(f"/api/dav/passwords/{item.pk}/").status_code == 204
    gone = dav_client.generic("PROPFIND", "/dav/", secure=True, HTTP_DEPTH="0", HTTP_AUTHORIZATION=basic("alice", raw))
    assert gone.status_code == 401


@pytest.mark.django_db
def test_password_limit(alice, bob, client_for, password):
    for i in range(auth.MAX_PASSWORDS):
        DavPassword.objects.create(user=alice, label=str(i), password_hash="x")
    r = client_for(alice).post("/api/dav/passwords/", {"label": "x", "password": password}, format="json")
    assert r.status_code == 400
    # Чужий пароль видалити не можна
    other = DavPassword.objects.create(user=bob, label="b", password_hash="x")
    assert client_for(alice).delete(f"/api/dav/passwords/{other.pk}/").status_code == 404


# ─────────────────────────── PROPFIND ───────────────────────────


@pytest.mark.django_db
def test_propfind_root_depth0(alice, dav):
    root = multistatus(dav("PROPFIND", "/dav/", PROPFIND_ALL, content_type="application/xml", HTTP_DEPTH="0"))
    assert hrefs(root) == ["/dav/"]
    prop = root.find("D:response/D:propstat/D:prop", NS)
    assert prop.find("D:resourcetype/D:collection", NS) is not None
    assert prop.find("D:supportedlock/D:lockentry", NS) is not None

    q = b'<D:propfind xmlns:D="DAV:"><D:prop><D:quota-available-bytes/><D:quota-used-bytes/><x:foo xmlns:x="urn:x"/></D:prop></D:propfind>'
    root = multistatus(dav("PROPFIND", "/dav/", q, content_type="application/xml", HTTP_DEPTH="0"))
    stats = root.findall("D:response/D:propstat", NS)
    ok = next(s for s in stats if "200" in s.find("D:status", NS).text)
    assert int(ok.find("D:prop/D:quota-available-bytes", NS).text) == alice.quota_bytes
    assert ok.find("D:prop/D:quota-used-bytes", NS).text == "0"
    missing = next(s for s in stats if "404" in s.find("D:status", NS).text)
    assert missing.find("D:prop/{urn:x}foo", NS) is not None


@pytest.mark.django_db
def test_propfind_infinity_rejected(dav):
    r = dav("PROPFIND", "/dav/", HTTP_DEPTH="infinity")
    assert r.status_code == 403
    assert b"propfind-finite-depth" in r.content
    assert dav("PROPFIND", "/dav/").status_code == 403  # без Depth = infinity


@pytest.mark.django_db
def test_mkcol_put_propfind_depth1_unicode(alice, dav):
    assert dav("MKCOL", "/dav/Документи").status_code == 201
    assert dav("MKCOL", "/dav/Документи").status_code == 405
    assert dav("MKCOL", "/dav/Нема/Вкладена").status_code == 409
    folder = Folder.objects.get(owner=alice, name="Документи")
    data = b"hello webdav" * 10
    path = "/dav/" + urllib.parse.quote("Документи") + "/" + urllib.parse.quote("звіт 1.txt")
    r = dav("PUT", path, data)
    assert r.status_code == 201, r.content
    f = File.objects.get(owner=alice, folder=folder, name="звіт 1.txt")
    assert f.status == File.Status.CLEAN and f.size == len(data)
    alice.refresh_from_db()
    assert alice.used_bytes == len(data)

    root = multistatus(dav("PROPFIND", "/dav/Документи/", content_type="application/xml", HTTP_DEPTH="1"))
    raw_hrefs = [h.text for h in root.findall("D:response/D:href", NS)]
    assert "/dav/%D0%94%D0%BE%D0%BA%D1%83%D0%BC%D0%B5%D0%BD%D1%82%D0%B8/" in raw_hrefs
    assert hrefs(root) == ["/dav/Документи/", "/dav/Документи/звіт 1.txt"]
    file_resp = root.findall("D:response", NS)[1]
    prop = file_resp.find("D:propstat/D:prop", NS)
    assert prop.find("D:getcontentlength", NS).text == str(len(data))
    assert prop.find("D:resourcetype", NS).find("D:collection", NS) is None
    assert prop.find("D:getetag", NS).text.startswith('"')
    assert prop.find("D:getlastmodified", NS).text.endswith("GMT")
    assert prop.find("D:creationdate", NS).text.endswith("Z")
    assert prop.find("D:displayname", NS).text == "звіт 1.txt"

    # NFD-ім'я від macOS знаходить той самий файл
    import unicodedata

    nfd = "/dav/" + urllib.parse.quote(unicodedata.normalize("NFD", "Документи")) + "/" + urllib.parse.quote("звіт 1.txt")
    assert body(dav("GET", nfd)) == data


# ─────────────────────────── GET / PUT ───────────────────────────


@pytest.mark.django_db
def test_get_head_range_and_only_clean(alice, dav):
    data = bytes(range(256)) * 4
    assert dav("PUT", "/dav/a.bin", data).status_code == 201
    r = dav("GET", "/dav/a.bin")
    assert r.status_code == 200 and body(r) == data
    assert r["Content-Type"] == "application/octet-stream" and "attachment" in r["Content-Disposition"]
    h = dav("HEAD", "/dav/a.bin")
    assert h.status_code == 200 and h["Content-Length"] == str(len(data)) and not h.content
    rr = dav("GET", "/dav/a.bin", HTTP_RANGE="bytes=10-19")
    assert rr.status_code == 206 and body(rr) == data[10:20]
    assert rr["Content-Range"] == f"bytes 10-19/{len(data)}"
    assert body(dav("GET", "/dav/a.bin", HTTP_RANGE="bytes=-5")) == data[-5:]
    assert dav("GET", "/dav/a.bin", HTTP_RANGE="bytes=5000-").status_code == 416

    File.objects.filter(owner=alice, name="a.bin").update(status=File.Status.SCANNING)
    r = dav("GET", "/dav/a.bin")
    assert r.status_code == 403
    File.objects.filter(owner=alice, name="a.bin").update(status=File.Status.INFECTED)
    assert dav("GET", "/dav/a.bin").status_code == 403
    assert dav("GET", "/dav/missing.bin").status_code == 404


@pytest.mark.django_db
def test_range_across_chunks(alice, dav, settings):
    settings.UPLOAD_CHUNK_SIZE = 100
    data = bytes(i % 251 for i in range(1000))
    assert dav("PUT", "/dav/c.bin", data).status_code == 201
    assert File.objects.get(owner=alice, name="c.bin").chunks_total == 10
    assert body(dav("GET", "/dav/c.bin", HTTP_RANGE="bytes=95-305")) == data[95:306]
    assert body(dav("GET", "/dav/c.bin")) == data


@pytest.mark.django_db
def test_put_overwrite_creates_version(alice, dav):
    assert dav("PUT", "/dav/doc.txt", b"first version").status_code == 201
    f = File.objects.get(owner=alice, name="doc.txt")
    r = dav("PUT", "/dav/doc.txt", b"second version!")
    assert r.status_code == 204
    f2 = File.objects.get(owner=alice, name="doc.txt")
    assert f2.pk == f.pk and f2.content_version > f.content_version
    assert body(dav("GET", "/dav/doc.txt")) == b"second version!"
    assert FileVersion.objects.filter(file=f).count() == 1
    alice.refresh_from_db()
    assert alice.used_bytes == len(b"first version") + len(b"second version!")


@pytest.mark.django_db
def test_empty_then_content_keeps_no_empty_version(alice, dav):
    assert dav("PUT", "/dav/new.txt", b"").status_code == 201
    assert dav("PUT", "/dav/new.txt", b"content").status_code == 204
    f = File.objects.get(owner=alice, name="new.txt")
    assert FileVersion.objects.filter(file=f).count() == 0
    assert body(dav("GET", "/dav/new.txt")) == b"content"


@pytest.mark.django_db
def test_put_quota_and_conflicts(alice, dav):
    alice.quota_bytes = 10
    alice.save(update_fields=["quota_bytes"])
    assert dav("PUT", "/dav/big.bin", b"x" * 11).status_code == 507
    assert not File.objects.filter(owner=alice, name="big.bin").exists()
    assert dav("PUT", "/dav/nofolder/x.txt", b"1").status_code == 409
    assert dav("MKCOL", "/dav/dir").status_code == 201
    assert dav("PUT", "/dav/dir", b"1").status_code == 405


@pytest.mark.django_db
def test_chunked_put_without_content_length(alice, dav_password):
    """Finder: chunked PUT без Content-Length (під ASGI тіло вже у тимчасовому файлі)."""
    from apps.dav.views import dav_view

    data = b"chunked body " * 100
    request = RequestFactory().generic("PUT", "/dav/chunked.txt", secure=True, HTTP_AUTHORIZATION=basic("alice", dav_password))
    request.META.pop("CONTENT_LENGTH", None)
    request.META["HTTP_TRANSFER_ENCODING"] = "chunked"
    request._stream = io.BytesIO(data)
    r = dav_view(request, "chunked.txt")
    assert r.status_code == 201, r.content
    assert File.objects.get(owner=alice, name="chunked.txt").size == len(data)


# ─────────────────────────── MOVE / COPY / DELETE ───────────────────────────


@pytest.mark.django_db
def test_move_copy_delete(alice, dav):
    assert dav("MKCOL", "/dav/src").status_code == 201
    assert dav("MKCOL", "/dav/dst").status_code == 201
    assert dav("PUT", "/dav/src/a.txt", b"AAA").status_code == 201
    assert dav("PUT", "/dav/src/b.txt", b"BBB").status_code == 201

    # MOVE (перейменування між папками)
    r = dav("MOVE", "/dav/src/a.txt", HTTP_DESTINATION="https://testserver/dav/dst/renamed.txt")
    assert r.status_code == 201, r.content
    assert dav("GET", "/dav/src/a.txt").status_code == 404
    assert body(dav("GET", "/dav/dst/renamed.txt")) == b"AAA"

    # Overwrite: F на наявне призначення → 412
    r = dav("MOVE", "/dav/src/b.txt", HTTP_DESTINATION="/dav/dst/renamed.txt", HTTP_OVERWRITE="F")
    assert r.status_code == 412
    # Overwrite: T → старе призначення в кошик
    r = dav("MOVE", "/dav/src/b.txt", HTTP_DESTINATION="/dav/dst/renamed.txt")
    assert r.status_code == 204
    assert body(dav("GET", "/dav/dst/renamed.txt")) == b"BBB"
    assert File.objects.filter(owner=alice, name="renamed.txt", deleted_at__isnull=False).count() == 1

    # COPY файлу
    r = dav("COPY", "/dav/dst/renamed.txt", HTTP_DESTINATION="/dav/copy.txt")
    assert r.status_code == 201
    assert body(dav("GET", "/dav/copy.txt")) == b"BBB"
    assert File.objects.get(owner=alice, name="copy.txt").pk != File.objects.get(owner=alice, name="renamed.txt", deleted_at__isnull=True).pk

    # COPY папки (рекурсивно)
    assert dav("COPY", "/dav/dst", HTTP_DESTINATION="/dav/dst2").status_code == 201
    assert body(dav("GET", "/dav/dst2/renamed.txt")) == b"BBB"
    # Папку не можна скопіювати в саму себе
    assert dav("COPY", "/dav/dst", HTTP_DESTINATION="/dav/dst/inner").status_code == 403

    # MOVE папки
    assert dav("MOVE", "/dav/dst2", HTTP_DESTINATION="/dav/src/moved").status_code == 201
    assert body(dav("GET", "/dav/src/moved/renamed.txt")) == b"BBB"

    # DELETE → кошик (не остаточно)
    assert dav("DELETE", "/dav/copy.txt").status_code == 204
    f = File.objects.get(owner=alice, name="copy.txt")
    assert f.deleted_at is not None
    assert dav("GET", "/dav/copy.txt").status_code == 404
    assert dav("DELETE", "/dav/src").status_code == 204
    assert Folder.objects.get(owner=alice, name="src", parent=None).deleted_at is not None
    assert dav("PROPFIND", "/dav/src/moved/", HTTP_DEPTH="0").status_code == 404
    assert dav("DELETE", "/dav/").status_code == 403


@pytest.mark.django_db
def test_destination_validation(dav):
    assert dav("PUT", "/dav/a.txt", b"A").status_code == 201
    assert dav("MOVE", "/dav/a.txt", HTTP_DESTINATION="https://evil.example/dav/b.txt").status_code == 502
    assert dav("MOVE", "/dav/a.txt", HTTP_DESTINATION="/api/files/").status_code == 502
    assert dav("MOVE", "/dav/a.txt", HTTP_DESTINATION="/dav/x%2Fy.txt").status_code == 400
    assert dav("MOVE", "/dav/a.txt", HTTP_DESTINATION="/dav/../b.txt").status_code == 400
    assert dav("MOVE", "/dav/a.txt").status_code == 400


@pytest.mark.django_db
def test_other_users_files_invisible(alice, bob, dav):
    from apps.storage import services

    f = services.start_upload(bob, name="secret.txt", size=0, folder=None)
    services.complete_upload(f)
    assert dav("GET", "/dav/secret.txt").status_code == 404
    root = multistatus(dav("PROPFIND", "/dav/", HTTP_DEPTH="1"))
    assert hrefs(root) == ["/dav/"]


# ─────────────────────────── LOCK / UNLOCK ───────────────────────────


LOCKINFO = (
    b'<?xml version="1.0"?><D:lockinfo xmlns:D="DAV:"><D:lockscope><D:exclusive/></D:lockscope>'
    b"<D:locktype><D:write/></D:locktype><D:owner><D:href>http://www.apple.com/webdav_fs/</D:href></D:owner></D:lockinfo>"
)


@pytest.mark.django_db
def test_lock_unlock(alice, dav):
    assert dav("PUT", "/dav/l.txt", b"one").status_code == 201
    r = dav("LOCK", "/dav/l.txt", LOCKINFO, content_type="application/xml", HTTP_TIMEOUT="Second-600")
    assert r.status_code == 200, r.content
    token = r["Lock-Token"].strip("<>")
    assert token.startswith("opaquelocktoken:")
    root = ET.fromstring(r.content)
    assert root.find("D:lockdiscovery/D:activelock/D:locktoken/D:href", NS).text == token

    # Другий клієнт без токена: запис, видалення, переміщення й повторне блокування заборонені
    assert dav("PUT", "/dav/l.txt", b"two").status_code == 423
    assert dav("DELETE", "/dav/l.txt").status_code == 423
    assert dav("MOVE", "/dav/l.txt", HTTP_DESTINATION="/dav/m.txt").status_code == 423
    assert dav("LOCK", "/dav/l.txt", LOCKINFO, content_type="application/xml").status_code == 423
    # Власник токена — може
    assert dav("PUT", "/dav/l.txt", b"two", HTTP_IF=f"(<{token}>)").status_code == 204
    # lockdiscovery у PROPFIND
    pf = multistatus(dav("PROPFIND", "/dav/l.txt", HTTP_DEPTH="0"))
    assert pf.find(".//D:lockdiscovery/D:activelock", NS) is not None
    # Оновлення блокування (без тіла)
    assert dav("LOCK", "/dav/l.txt", HTTP_IF=f"(<{token}>)", HTTP_TIMEOUT="Second-60").status_code == 200
    assert dav("UNLOCK", "/dav/l.txt", HTTP_LOCK_TOKEN="<opaquelocktoken:00000000-0000-0000-0000-000000000000>").status_code == 409
    assert dav("UNLOCK", "/dav/l.txt", HTTP_LOCK_TOKEN=f"<{token}>").status_code == 204
    assert dav("PUT", "/dav/l.txt", b"three").status_code == 204


@pytest.mark.django_db
def test_lock_unmapped_creates_empty_file_and_folder_lock(alice, dav):
    r = dav("LOCK", "/dav/new.docx", LOCKINFO, content_type="application/xml")
    assert r.status_code == 201
    token = r["Lock-Token"].strip("<>")
    f = File.objects.get(owner=alice, name="new.docx")
    assert f.size == 0
    assert dav("PUT", "/dav/new.docx", b"data", HTTP_IF=f"</dav/new.docx> (<{token}>)").status_code == 204

    # Блокування папки з Depth: infinity захищає її вміст
    assert dav("MKCOL", "/dav/locked").status_code == 201
    r = dav("LOCK", "/dav/locked/", LOCKINFO, content_type="application/xml", HTTP_DEPTH="infinity")
    assert r.status_code == 200
    ftoken = r["Lock-Token"].strip("<>")
    assert dav("PUT", "/dav/locked/x.txt", b"x").status_code == 423
    assert dav("PUT", "/dav/locked/x.txt", b"x", HTTP_IF=f"(<{ftoken}>)").status_code == 201
    assert dav("UNLOCK", "/dav/locked/", HTTP_LOCK_TOKEN=f"<{ftoken}>").status_code == 204


def test_lock_timeout_parsing():
    assert locks.parse_timeout("Second-600") == 600
    assert locks.parse_timeout("Infinite, Second-4100000000") == locks.MAX_TIMEOUT
    assert locks.parse_timeout("Second-1") == locks.MIN_TIMEOUT
    assert locks.parse_timeout(None) == locks.DEFAULT_TIMEOUT


# ─────────────────────────── PROPPATCH ───────────────────────────


@pytest.mark.django_db
def test_proppatch_accepts_apple_props(dav):
    assert dav("PUT", "/dav/p.txt", b"p").status_code == 201
    body_xml = (
        b'<?xml version="1.0"?><D:propertyupdate xmlns:D="DAV:" xmlns:A="http://www.apple.com/webdav_fs/props/">'
        b"<D:set><D:prop><A:appledoubleheader>AAAA</A:appledoubleheader></D:prop></D:set></D:propertyupdate>"
    )
    root = multistatus(dav("PROPPATCH", "/dav/p.txt", body_xml, content_type="application/xml"))
    stat = root.find("D:response/D:propstat", NS)
    assert "200" in stat.find("D:status", NS).text
    assert stat.find("D:prop/{http://www.apple.com/webdav_fs/props/}appledoubleheader", NS) is not None


# ─────────────────────────── Безпека розбору ───────────────────────────


@pytest.mark.django_db
def test_xxe_and_entities_rejected(dav):
    xxe = (
        b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b'<D:propfind xmlns:D="DAV:"><D:prop><D:displayname>&xxe;</D:displayname></D:prop></D:propfind>'
    )
    r = dav("PROPFIND", "/dav/", xxe, content_type="application/xml", HTTP_DEPTH="0")
    assert r.status_code == 400
    assert b"passwd" not in r.content and b"root:" not in r.content
    bomb = b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><D:propfind xmlns:D="DAV:">&b;</D:propfind>'
    assert dav("PROPFIND", "/dav/", bomb, content_type="application/xml", HTTP_DEPTH="0").status_code == 400
    assert dav("PROPFIND", "/dav/", b"<not-closed", content_type="application/xml", HTTP_DEPTH="0").status_code == 400


@pytest.mark.django_db
def test_path_traversal_rejected(alice, dav):
    assert dav("PUT", "/dav/a/../b.txt", b"x").status_code == 400
    assert dav("PUT", "/dav/%2e%2e/b.txt", b"x").status_code == 400
    assert dav("PROPFIND", "/dav/./", HTTP_DEPTH="0").status_code == 400
    assert dav("PUT", "/dav/a%5Cb.txt", b"x").status_code == 400
    assert not File.objects.filter(owner=alice).exists()
    with pytest.raises(paths.BadPath):
        paths.check_raw("/dav/a%2Fb")
    with pytest.raises(paths.BadPath):
        paths.check_raw(b"/dav/a%2fb")
    paths.check_raw("/dav/%D0%94%D0%BE")
    with pytest.raises(paths.BadPath):
        paths.split("a//b")


# ─────────────────────────── Ліміти тіла запиту ───────────────────────────


def _run_asgi(path, headers, body=b""):
    from apps.core.asgi_limits import BodyLimitMiddleware

    calls = {}

    async def app(scope, receive, send):
        chunks = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                calls["disconnect"] = True
                break
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        calls["body"] = b"".join(chunks)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent = []

    async def send(message):
        sent.append(message)

    messages = [{"type": "http.request", "body": body[:5], "more_body": True}, {"type": "http.request", "body": body[5:], "more_body": False}]

    async def receive():
        return messages.pop(0)

    scope = {"type": "http", "path": path, "headers": headers}
    asyncio.run(BodyLimitMiddleware(app, 20 * 1024 * 1024)(scope, receive, send))
    return sent[0]["status"], calls


def test_asgi_chunked_allowed_only_for_dav(settings):
    status, calls = _run_asgi("/dav/file.bin", [(b"transfer-encoding", b"chunked")], b"0123456789")
    assert status == 200 and calls["body"] == b"0123456789"
    status, calls = _run_asgi("/api/files/uploads/", [(b"transfer-encoding", b"chunked")], b"0123456789")
    assert status == 411 and not calls
    # Ліміт для /dav — MAX_FILE_SIZE
    settings.MAX_FILE_SIZE = 5
    status, calls = _run_asgi("/dav/file.bin", [(b"transfer-encoding", b"chunked")], b"0123456789")
    assert calls.get("disconnect")
    status, _ = _run_asgi("/dav/f", [(b"transfer-encoding", b"chunked"), (b"x-expected-entity-length", b"100")])
    assert status == 413
    status, _ = _run_asgi("/dav/f", [(b"content-length", b"100")])
    assert status == 413


def test_django_middleware_chunked_scoped(settings):
    from django.http import HttpResponse

    from apps.core.middleware import MaxBodySizeMiddleware

    mw = MaxBodySizeMiddleware(lambda request: HttpResponse("ok"))
    rf = RequestFactory()

    def make(path, **meta):
        request = rf.generic("PUT", path)
        request.META.pop("CONTENT_LENGTH", None)
        request.META.update(meta)
        return request

    assert mw(make("/dav/x", HTTP_TRANSFER_ENCODING="chunked")).status_code == 200
    assert mw(make("/api/x", HTTP_TRANSFER_ENCODING="chunked")).status_code == 411
    big = str(settings.MAX_REQUEST_BODY + 1)
    assert mw(make("/api/x", CONTENT_LENGTH=big)).status_code == 413
    assert mw(make("/dav/x", CONTENT_LENGTH=big)).status_code == 200
    assert mw(make("/dav/x", CONTENT_LENGTH=str(settings.MAX_FILE_SIZE + 1))).status_code == 413
