"""Регресійні тести для виправлень з аудиту безпеки."""

import asyncio
import io
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone as dt_tz
from pathlib import Path

import pytest
from django.test import RequestFactory
from rest_framework.test import APIClient

from apps.accounts import twofactor
from apps.accounts.models import BackupCode
from apps.core.models import Notification
from apps.storage.models import PublicLink

from .test_accounts import _enable_totp, login
from .test_previews_office import minimal_pdf
from .test_storage import body, upload

BACKEND_DIR = Path(__file__).resolve().parent.parent


# ═══════════════════ Налаштування: ключі розробки та DEBUG ═══════════════════


def _import_settings(tmp_path, **env):
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "DOMAIN", "ALLOW_INSECURE"))}
    clean.update({"SECRETS_DIR": str(tmp_path), "PYTHONPATH": os.pathsep.join(sys.path)}, **env)
    return subprocess.run(
        [sys.executable, "-c", "import config.settings"], cwd=BACKEND_DIR, env=clean, capture_output=True, text=True
    )


def test_debug_refused_on_public_domain(tmp_path):
    r = _import_settings(tmp_path, DJANGO_DEBUG="true", DOMAIN="cloud.example.com")
    assert r.returncode != 0 and "DJANGO_DEBUG" in r.stderr


def test_missing_secret_is_fatal_even_with_debug_on_localhost(tmp_path):
    r = _import_settings(tmp_path, DJANGO_DEBUG="true", DOMAIN="localhost")
    assert r.returncode != 0 and "DJANGO_SECRET_KEY" in r.stderr


def test_dev_keys_only_when_explicitly_allowed(tmp_path):
    r = _import_settings(tmp_path, DJANGO_DEBUG="true", DOMAIN="localhost", ALLOW_INSECURE_DEV_KEYS="true")
    assert r.returncode == 0, r.stderr
    # на публічному домені прапорець не діє
    r = _import_settings(tmp_path, DOMAIN="cloud.example.com", ALLOW_INSECURE_DEV_KEYS="true")
    assert r.returncode != 0


# ═══════════════════ Адміністрування ═══════════════════


@pytest.mark.django_db
def test_admin_needs_mfa_in_this_session(make_user, client_for, settings):
    settings.REQUIRE_2FA_FOR_STAFF = True
    admin = make_user("boss", is_staff=True)
    _enable_totp(admin)
    assert client_for(admin).get("/api/admin/users/").status_code == 403
    assert client_for(admin, mfa=True).get("/api/admin/users/").status_code == 200


@pytest.mark.django_db
def test_staff_cannot_manage_other_admins(make_user, client_for, settings):
    settings.REQUIRE_2FA_FOR_STAFF = True
    staff = make_user("helper", is_staff=True)
    other_staff = make_user("helper2", is_staff=True)
    root = make_user("chief", is_staff=True, is_superuser=True)
    regular = make_user("carol")
    for u in (staff, other_staff, root):
        _enable_totp(u)
    c = client_for(staff, mfa=True)

    assert c.post(f"/api/admin/users/{other_staff.pk}/reset_2fa/").status_code == 403
    assert c.patch(f"/api/admin/users/{root.pk}/", {"is_active": False}, format="json").status_code == 403
    root.refresh_from_db()
    assert root.is_active and root.has_2fa
    # звичайним користувачем керувати можна
    assert c.patch(f"/api/admin/users/{regular.pk}/", {"quota_gb": 1}, format="json").status_code == 200

    # суперкористувач може скинути 2FA адміністратора — і всі його сесії завершуються
    victim_client = APIClient()
    victim_client.force_login(other_staff)
    assert client_for(root, mfa=True).post(f"/api/admin/users/{other_staff.pk}/reset_2fa/").status_code == 200
    other_staff.refresh_from_db()
    assert not other_staff.has_2fa
    assert victim_client.get("/api/auth/me/").json()["authenticated"] is False


@pytest.mark.django_db
def test_django_admin_hides_sessions_and_locks_file_status():
    from apps.core.admin_site import admin_site
    from apps.accounts.models import UserSession
    from apps.storage.models import File

    assert UserSession not in admin_site._registry
    file_admin = admin_site._registry[File]
    for field in ("status", "owner", "sha256", "mime_type", "scan_detail"):
        assert field in file_admin.readonly_fields


# ═══════════════════ 2FA: ліміт на акаунт ═══════════════════


@pytest.mark.django_db
def test_2fa_bruteforce_locks_account_even_with_correct_password(alice, password):
    secret, _ = _enable_totp(alice)
    c = APIClient()
    failures = 0
    while failures < 10:
        assert login(c, "alice", password).json()["status"] == "2fa_required"
        for _ in range(5):
            r = c.post("/api/auth/login/2fa/", {"code": "000000"}, format="json")
            assert r.status_code == 400
            failures += 1
    # успішний пароль НЕ скидає лічильник 2FA
    assert login(c, "alice", password).status_code == 429
    assert Notification.objects.filter(user=alice, kind="security").exists()


# ═══════════════════ Резервні коди і сесії ═══════════════════


@pytest.mark.django_db
def test_backup_codes_are_80_bit_and_old_codes_still_work(alice):
    _, codes = _enable_totp(alice)
    assert all(re.fullmatch(r"[a-z2-7]{4}(-[a-z2-7]{4}){3}", code) for code in codes)
    alice.refresh_from_db()
    assert twofactor.verify_second_factor(alice, codes[0]) == "backup_code"
    assert twofactor.verify_second_factor(alice, codes[0]) is None  # одноразовий
    BackupCode.objects.create(user=alice, code_hash=BackupCode.hash_code("abcde-12345"))
    assert twofactor.verify_second_factor(alice, "abcde-12345") == "backup_code"


@pytest.mark.django_db
def test_session_ids_do_not_leak_session_key(alice, password):
    c = APIClient()
    assert login(c, "alice", password).json()["status"] == "ok"
    key = c.cookies["bc_session"].value
    sessions = c.get("/api/account/sessions/").json()
    assert len(sessions) == 1
    sid = sessions[0]["id"]
    assert len(sid) == 20 and sid not in key and not key.startswith(sid[:6])
    assert c.delete(f"/api/account/sessions/{key[:12]}/").status_code in (400, 404)  # старий формат не працює
    assert c.delete(f"/api/account/sessions/{sid}/").status_code == 204
    assert c.get("/api/auth/me/").json()["authenticated"] is False


# ═══════════════════ Зарезервовані імена ═══════════════════


@pytest.mark.parametrize("name", ["security", "ad.min", "post_master", "ssl-admin", "postmaster1", "support"])
@pytest.mark.django_db
def test_reserved_usernames_rejected(name, settings):
    settings.REGISTRATION_OPEN = True
    r = APIClient().post(
        "/api/auth/register/",
        {"username": name, "email": f"x{abs(hash(name))}@example.org", "password": "Very-Strong-Passw0rd!"},
        format="json",
    )
    assert r.status_code == 400
    assert "username" in r.json()


@pytest.mark.django_db
def test_reserved_name_gets_no_mailbox(make_user):
    from apps.mail.models import Mailbox

    u = make_user("abuse")
    assert not Mailbox.objects.filter(user=u).exists()
    v = make_user("dmitro")
    assert Mailbox.objects.filter(user=v).exists()


# ═══════════════════ Публічні посилання ═══════════════════


@pytest.mark.django_db
def test_public_link_short_password_rejected(alice, client_for):
    c = client_for(alice)
    info = upload(c, b"data")
    r = c.post("/api/files/links/", {"file": info["id"], "password": "1234567"}, format="json")
    assert r.status_code == 400


@pytest.mark.django_db
def test_public_link_password_bruteforce_locks_link(alice, client_for, settings):
    c = client_for(alice)
    info = upload(c, b"secret bytes")
    url = c.post("/api/files/links/", {"file": info["id"], "password": "correct-horse"}, format="json").json()["url"]
    token = url.split("#", 1)[1]
    anon = APIClient()
    for _ in range(settings.PUBLIC_LINK_MAX_FAILURES):
        r = anon.post("/api/public/authorize/", {"token": token, "password": "guess"}, format="json")
        assert r.status_code == 403
    # тепер навіть правильний пароль — 429, власника сповіщено
    r = anon.post("/api/public/authorize/", {"token": token, "password": "correct-horse"}, format="json")
    assert r.status_code == 429
    assert Notification.objects.filter(user=alice, kind="security").exists()


@pytest.mark.django_db
def test_old_token_in_path_endpoints_removed(alice, client_for):
    c = client_for(alice)
    info = upload(c, b"x")
    token = c.post("/api/files/links/", {"file": info["id"]}, format="json").json()["url"].split("#", 1)[1]
    assert not PublicLink.objects.filter(token_hash=token).exists()
    assert APIClient().get(f"/api/public/{token}/").status_code == 404


# ═══════════════════ Перегляд на окремому origin ═══════════════════


@pytest.mark.django_db
def test_main_domain_never_serves_inline(alice, client_for):
    c = client_for(alice)
    info = upload(c, minimal_pdf(), name="doc.pdf")
    r = c.get(f"/api/files/items/{info['id']}/download/?inline=1")
    assert r.status_code == 200
    assert r["Content-Disposition"].startswith("attachment")
    assert r["Content-Type"] == "application/octet-stream"
    assert "sandbox" in r["Content-Security-Policy"]


@pytest.mark.django_db
def test_preview_ticket_served_only_on_usercontent_host(alice, client_for, settings):
    c = client_for(alice)
    info = upload(c, minimal_pdf(), name="doc.pdf")
    r = c.get(f"/api/files/items/{info['id']}/preview/")
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith(f"https://{settings.USERCONTENT_HOST}/api/preview/")
    path = url.split(settings.USERCONTENT_HOST, 1)[1]

    anon = APIClient()
    # на основному домені (або будь-якому іншому хості) — 404
    assert anon.get(path).status_code == 404
    assert anon.get(path, HTTP_HOST=settings.DOMAIN).status_code == 404
    r = anon.get(path, HTTP_HOST=settings.USERCONTENT_HOST)
    assert r.status_code == 200
    assert r["Content-Disposition"].startswith("inline")
    assert f"frame-ancestors https://{settings.DOMAIN}" in r["Content-Security-Policy"]
    assert body(r) == minimal_pdf()
    # вигаданий квиток
    assert anon.get("/api/preview/forged/", HTTP_HOST=settings.USERCONTENT_HOST).status_code == 404


@pytest.mark.django_db
def test_preview_ticket_rechecks_access(alice, bob, client_for, settings):
    ca = client_for(alice)
    folder = ca.post("/api/files/folders/", {"name": "shared"}, format="json").json()
    info = upload(ca, b"plain text here", name="note.txt", folder=folder["id"])
    share = ca.post("/api/files/shares/", {"username": "bob", "folder": folder["id"]}, format="json").json()
    url = client_for(bob).get(f"/api/files/items/{info['id']}/preview/").json()["url"]
    path = url.split(settings.USERCONTENT_HOST, 1)[1]
    anon = APIClient()
    assert anon.get(path, HTTP_HOST=settings.USERCONTENT_HOST).status_code == 200
    assert ca.delete(f"/api/files/shares/{share['id']}/").status_code == 204
    assert anon.get(path, HTTP_HOST=settings.USERCONTENT_HOST).status_code == 404


@pytest.mark.django_db
def test_preview_refuses_unsafe_types(alice, client_for):
    c = client_for(alice)
    info = upload(c, b"<html><script>alert(1)</script></html>", name="x.html")
    assert c.get(f"/api/files/items/{info['id']}/preview/").status_code == 400


# ═══════════════════ ONLYOFFICE ═══════════════════


@pytest.fixture
def office_on(settings):
    settings.OFFICE_ENABLED = True
    settings.OFFICE_JWT_SECRET = "test-office-secret"
    return settings


@pytest.mark.django_db
def test_office_config_hardened(alice, client_for, office_on):
    c = client_for(alice)
    info = upload(c, b"PK fake docx", name="a.docx")
    data = c.get(f"/api/office/config/{info['id']}/").json()
    assert data["api_url"].startswith(f"https://{office_on.OFFICE_HOST}/")
    custom = data["config"]["editorConfig"]["customization"]
    assert custom["macros"] is False and custom["plugins"] is False
    assert data["config"]["exp"] - time.time() <= office_on.OFFICE_EDITOR_TOKEN_TTL + 5


@pytest.mark.django_db
def test_office_file_token_short_lived_and_internal_only(alice, client_for, office_on, monkeypatch):
    c = client_for(alice)
    info = upload(c, b"PK fake docx", name="a.docx")
    doc_url = c.get(f"/api/office/config/{info['id']}/").json()["config"]["document"]["url"]
    path = doc_url.split("backend:8000", 1)[1]
    anon = APIClient()
    assert anon.get(path).status_code == 200
    # через публічний домен (Traefik) — закрито
    assert anon.get(path, HTTP_HOST=office_on.DOMAIN).status_code == 404
    # через 6 хвилин токен прострочений
    import django.core.signing as signing

    real = time.time
    monkeypatch.setattr(signing.time, "time", lambda: real() + 6 * 60)
    assert anon.get(path).status_code == 404


# ═══════════════════ Календар: «важкі» правила ═══════════════════


def test_impossible_rrule_rejected_fast():
    from rest_framework.exceptions import ValidationError

    from apps.calendars.recurrence import validate_rrule

    start = datetime(2026, 1, 1, 9, tzinfo=dt_tz.utc)
    t0 = time.monotonic()
    with pytest.raises(ValidationError):
        validate_rrule("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=31", start)
    with pytest.raises(ValidationError):
        validate_rrule("FREQ=DAILY;INTERVAL=0", start)
    with pytest.raises(ValidationError):
        validate_rrule("FREQ=DAILY", datetime(1200, 1, 1, tzinfo=dt_tz.utc))
    assert time.monotonic() - t0 < 1.0
    assert validate_rrule("FREQ=WEEKLY;BYDAY=MO,WE", start) == "FREQ=WEEKLY;BYDAY=MO,WE"
    # 29 лютого — раз на 4 роки, але правило коректне
    assert validate_rrule("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29", start)


def test_occurrences_bounded_for_stored_bad_rule():
    from types import SimpleNamespace

    from apps.calendars.recurrence import occurrences

    start = datetime(2026, 1, 1, 9, tzinfo=dt_tz.utc)
    ev = SimpleNamespace(start=start, end=start, rrule="FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=31")
    t0 = time.monotonic()
    assert occurrences(ev, start, datetime(2026, 12, 31, tzinfo=dt_tz.utc)) == []
    assert time.monotonic() - t0 < 0.5


@pytest.mark.django_db
def test_recurring_events_capped(alice, client_for, settings):
    settings.MAX_RECURRING_EVENTS_PER_USER = 1
    c = client_for(alice)
    cal = c.get("/api/calendars/").json()[0]
    ev = {"calendar": cal["id"], "title": "x", "start": "2026-03-02T09:00:00Z", "end": "2026-03-02T10:00:00Z"}
    assert c.post("/api/calendars/events/", {**ev, "rrule": "FREQ=WEEKLY"}, format="json").status_code == 201
    assert c.post("/api/calendars/events/", {**ev, "rrule": "FREQ=DAILY"}, format="json").status_code == 400
    assert c.post("/api/calendars/events/", ev, format="json").status_code == 201


# ═══════════════════ Чат ═══════════════════


@pytest.mark.django_db
def test_chat_only_with_discoverable_or_known_users(alice, bob, client_for):
    bob.preferences.discoverable = False
    bob.preferences.save()
    ca = client_for(alice)
    assert ca.post("/api/chat/conversations/", {"usernames": ["bob"]}, format="json").status_code == 400
    # Боб сам починає розмову — тепер Аліса може відповідати й створювати групи з ним
    assert client_for(bob).post("/api/chat/conversations/", {"usernames": ["alice"]}, format="json").status_code == 201
    assert ca.post("/api/chat/conversations/", {"usernames": ["bob"]}, format="json").status_code == 201


# ═══════════════════ Транспорт: тіло запиту, IP ═══════════════════


def test_chunked_without_length_rejected_by_django_middleware():
    from apps.core.middleware import MaxBodySizeMiddleware

    req = RequestFactory().post("/api/x/", data=b"", content_type="application/json")
    req.META.pop("CONTENT_LENGTH", None)
    req.META["HTTP_TRANSFER_ENCODING"] = "chunked"
    resp = MaxBodySizeMiddleware(lambda r: None)(req)
    assert resp.status_code == 411


def test_asgi_body_limit():
    from apps.core.asgi_limits import BodyLimitMiddleware

    called = {}

    async def app(scope, receive, send):
        called["yes"] = True
        msg = await receive()
        called["type"] = msg["type"]

    async def run(headers, body=b""):
        sent = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(msg):
            sent.append(msg)

        called.clear()
        scope = {"type": "http", "method": "POST", "headers": headers}
        await BodyLimitMiddleware(app, 100)(scope, receive, send)
        return sent[0]["status"] if sent else None

    assert asyncio.run(run([(b"transfer-encoding", b"chunked")])) == 411
    assert asyncio.run(run([(b"content-length", b"1000")])) == 413
    assert asyncio.run(run([(b"content-length", b"5")], b"12345")) is None and called["type"] == "http.request"
    # більше байтів, ніж оголошено → застосунок отримує disconnect
    asyncio.run(run([(b"content-length", b"2")], b"12345"))
    assert called["type"] == "http.disconnect"


def test_client_ip_ignores_forwarded_for_header():
    from apps.core.audit import client_ip

    req = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="6.6.6.6", REMOTE_ADDR="10.1.2.3")
    assert client_ip(req) == "10.1.2.3"


# ═══════════════════ WebSocket: примусове закриття ═══════════════════


@pytest.mark.django_db
def test_logout_and_revoke_close_websockets(alice, password, monkeypatch):
    from apps.core import realtime

    sent = []

    class Layer:
        async def group_send(self, group, message):
            sent.append((group, message["type"]))

    monkeypatch.setattr(realtime, "get_channel_layer", lambda: Layer())
    c = APIClient()
    login(c, "alice", password)
    key = c.cookies["bc_session"].value
    c.post("/api/auth/logout/")
    assert (realtime.session_group(key), "force_close") in sent


# ═══════════════════ Пошта: IMAP-пошук ═══════════════════


def test_imap_search_uses_literal_and_strips_crlf():
    from apps.mail import imap

    class Conn:
        literal = None
        calls = []

        def uid(self, *args):
            self.calls.append((args, self.literal))
            self.literal = None
            return "OK", [b"3 7"]

    conn = Conn()
    found = imap._search(conn, 'Привіт\r\nA1 LOGOUT "x"')
    assert found == {3, 7}
    for args, lit in conn.calls:
        assert args[:3] == ("SEARCH", "CHARSET", "UTF-8")
        assert b"\r" not in lit and b"\n" not in lit
        assert lit.decode() == 'ПривітA1 LOGOUT "x"'


# ═══════════════════ Мініатюри: ізольований рендер ═══════════════════


def test_thumbrender_rejects_pixel_bomb():
    from PIL import Image

    from apps.storage import thumbrender

    buf = io.BytesIO()
    Image.new("1", (7000, 7000)).save(buf, format="PNG")
    with pytest.raises(Exception):
        thumbrender.render("image", buf.getvalue())


@pytest.mark.django_db
def test_worker_uses_thumbnailer_service(alice, client_for, settings, monkeypatch):
    from apps.storage import thumbnails, thumbrender

    from .test_previews_office import png_bytes

    settings.THUMBNAILER_URL = "http://thumbnailer:8100"
    calls = []

    class Resp:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return self.data[:n]

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        return Resp(thumbrender.render("image", req.data))

    monkeypatch.setattr(thumbnails.urllib.request, "urlopen", fake_urlopen)
    info = upload(client_for(alice), png_bytes(), name="p.png")
    assert calls == ["http://thumbnailer:8100/render?kind=image"]
    from apps.storage.models import File

    assert File.objects.get(pk=info["id"]).has_thumbnail

    # некоректна відповідь сервісу → мініатюри немає
    monkeypatch.setattr(thumbnails.urllib.request, "urlopen", lambda req, timeout: Resp(b"<html>"))
    info2 = upload(client_for(alice), png_bytes(color=(1, 2, 3)), name="q.png")
    assert not File.objects.get(pk=info2["id"]).has_thumbnail


def test_throttle_identity_ignores_forwarded_for():
    from rest_framework.request import Request
    from rest_framework.throttling import BaseThrottle

    req = Request(RequestFactory().get("/", HTTP_X_FORWARDED_FOR="1.1.1.1", REMOTE_ADDR="10.9.8.7"))
    assert BaseThrottle().get_ident(req) == "10.9.8.7"
