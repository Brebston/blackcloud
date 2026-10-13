"""Тести: фото профілю, пошук фото для веб-пошти, блокування акаунтів, видалення скриньок."""

import io
from contextlib import contextmanager

import pytest
from django.contrib.sessions.models import Session
from rest_framework.test import APIClient

from apps.accounts.models import User, UserAvatar, UserSession
from apps.mail.models import Alias, MailDomain, Mailbox

from .test_accounts import _enable_totp


def _image(fmt="PNG", size=(40, 30), exif=False):
    from PIL import Image

    img = Image.new("RGB", size, (200, 30, 30))
    out = io.BytesIO()
    kwargs = {}
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "SecretCameraMaker"  # Make
        kwargs["exif"] = ex.tobytes()
    img.save(out, format=fmt, **kwargs)
    out.seek(0)
    out.name = f"photo.{fmt.lower()}"
    return out


@pytest.fixture
def admin(make_user, settings):
    settings.REQUIRE_2FA_FOR_STAFF = True
    u = make_user("chief", is_staff=True, is_superuser=True)
    _enable_totp(u)
    return u


@pytest.fixture
def staff(make_user, settings):
    settings.REQUIRE_2FA_FOR_STAFF = True
    u = make_user("helper", is_staff=True)
    _enable_totp(u)
    return u


def _mailbox(user, local=None):
    domain, _ = MailDomain.objects.get_or_create(name="example.com")
    mb = Mailbox.objects.filter(user=user, local_part=local or user.username).first()
    return mb or Mailbox.objects.create(user=user, domain=domain, local_part=local or user.username)


# ═══════════════════ Фото профілю ═══════════════════


@pytest.mark.django_db
def test_avatar_upload_processed_and_served(alice, bob, client_for):
    c = client_for(alice)
    assert c.get("/api/account/avatar/").json()["status"] == "none"
    r = c.post("/api/account/avatar/", {"file": _image("JPEG", (300, 200), exif=True)}, format="multipart")
    assert r.status_code == 202, r.content
    # Celery у тестах виконується одразу (рендер — той самий модуль, що й у thumbnailer)
    st = c.get("/api/account/avatar/").json()
    assert st["status"] == "ready" and st["avatar_url"]
    me = c.get("/api/auth/me/").json()["user"]
    assert me["avatar_url"] == st["avatar_url"]
    av = UserAvatar.objects.get(user=alice)
    assert av.pending == b"" or bytes(av.pending) == b""
    assert av.pending_id is None

    # Будь-який користувач з сесією бачить фото
    r = client_for(bob).get(me["avatar_url"])
    assert r.status_code == 200
    body = r.content
    assert body[:4] == b"RIFF" and body[8:12] == b"WEBP"
    assert b"SecretCameraMaker" not in body and b"Exif" not in body
    assert r["Content-Type"] == "image/webp"
    assert r["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in r["Content-Security-Policy"]
    assert r["Cache-Control"].startswith("private")
    from PIL import Image

    assert Image.open(io.BytesIO(body)).size == (256, 256)
    # ETag → 304
    r2 = client_for(bob).get(me["avatar_url"], HTTP_IF_NONE_MATCH=r["ETag"])
    assert r2.status_code == 304
    # Без входу — ні
    assert APIClient().get(me["avatar_url"]).status_code in (401, 403)

    # Видалення
    assert c.delete("/api/account/avatar/").status_code == 204
    assert c.get("/api/auth/me/").json()["user"]["avatar_url"] is None
    assert client_for(bob).get(f"/api/users/{alice.pk}/avatar/").status_code == 404


@pytest.mark.django_db
def test_avatar_rejects_non_images_and_big_files(alice, client_for):
    c = client_for(alice)
    fake = io.BytesIO(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
    fake.name = "x.png"
    assert c.post("/api/account/avatar/", {"file": fake}, format="multipart").status_code == 400
    big = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024 + 10))
    big.name = "big.png"
    assert c.post("/api/account/avatar/", {"file": big}, format="multipart").status_code == 400
    assert c.post("/api/account/avatar/", {}, format="multipart").status_code == 400
    assert not UserAvatar.objects.filter(user=alice).exists()


@pytest.mark.django_db
def test_avatar_broken_image_marks_failed(alice, client_for):
    c = client_for(alice)
    broken = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
    broken.name = "broken.png"
    r = c.post("/api/account/avatar/", {"file": broken}, format="multipart")
    assert r.status_code in (202, 400)
    if r.status_code == 202:
        assert c.get("/api/account/avatar/").json()["status"] == "failed"
    alice.refresh_from_db()
    assert alice.avatar_version == 0


@pytest.mark.django_db
def test_avatar_lookup_for_webmail(alice, bob, client_for):
    mb = _mailbox(alice)
    Alias.objects.create(source="old-alice@example.com", destination=mb.address, owner=None)
    _mailbox(bob)
    client_for(alice).post("/api/account/avatar/", {"file": _image()}, format="multipart")
    alice.refresh_from_db()
    c = client_for(bob)
    emails = f"{mb.address.upper()},old-alice@example.com,bob@example.com,stranger@gmail.com"
    data = c.get("/api/users/avatars/", {"emails": emails}).json()
    assert data == {mb.address: alice.avatar_url, "old-alice@example.com": alice.avatar_url}
    too_many = ",".join(f"u{i}@example.com" for i in range(101))
    assert c.get("/api/users/avatars/", {"emails": too_many}).status_code == 400
    assert APIClient().get("/api/users/avatars/", {"emails": mb.address}).status_code in (401, 403)
    # Заблокований — без фото
    User.objects.filter(pk=alice.pk).update(is_active=False)
    assert c.get("/api/users/avatars/", {"emails": mb.address}).json() == {}


# ═══════════════════ Блокування ═══════════════════


@pytest.mark.django_db
def test_admin_blocks_and_unblocks_user(alice, admin, client_for, password):
    # Активна сесія Аліси
    ac = APIClient()
    assert ac.post("/api/auth/login/", {"login": "alice", "password": password}, format="json").status_code == 200
    assert UserSession.objects.filter(user=alice).exists()
    c = client_for(admin, mfa=True)
    url = f"/api/admin/users/{alice.pk}/block/"
    assert c.post(url, {"reason": "", "password": password}, format="json").status_code == 400
    assert c.post(url, {"reason": "x" * 501, "password": password}, format="json").status_code == 400
    assert c.post(url, {"reason": "спам", "password": "wrong-password"}, format="json").status_code == 400
    r = c.post(url, {"reason": "Розсилав спам", "password": password}, format="json")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["is_active"] is False and body["blocked_reason"] == "Розсилав спам" and body["blocked_by"] == "chief"
    alice.refresh_from_db()
    assert not alice.is_active and alice.blocked_at
    assert not UserSession.objects.filter(user=alice).exists()
    assert ac.get("/api/auth/me/").json()["authenticated"] is False
    # Помилка входу — загальна (без розкриття факту блокування)
    r = APIClient().post("/api/auth/login/", {"login": "alice", "password": password}, format="json")
    r_unknown = APIClient().post("/api/auth/login/", {"login": "nobody", "password": password}, format="json")
    assert r.status_code == 400 and r.json() == r_unknown.json()

    listed = {u["username"]: u for u in c.get("/api/admin/users/").json()["results"]}
    assert listed["alice"]["blocked_reason"] == "Розсилав спам"

    r = c.post(f"/api/admin/users/{alice.pk}/unblock/", {}, format="json")
    assert r.status_code == 200 and r.json()["is_active"] is True and r.json()["blocked_reason"] == ""
    alice.refresh_from_db()
    assert alice.is_active and alice.blocked_at is None and alice.blocked_by is None


@pytest.mark.django_db
def test_block_rules(alice, admin, staff, make_user, client_for, password):
    c = client_for(staff, mfa=True)
    # Себе — ні
    assert c.post(f"/api/admin/users/{staff.pk}/block/", {"reason": "x", "password": password}, format="json").status_code == 400
    # Іншого адміністратора чи суперкористувача — лише суперкористувач
    assert c.post(f"/api/admin/users/{admin.pk}/block/", {"reason": "x", "password": password}, format="json").status_code == 403
    other = make_user("other", is_staff=True)
    assert c.post(f"/api/admin/users/{other.pk}/block/", {"reason": "x", "password": password}, format="json").status_code == 403
    # Звичайного користувача — так
    assert c.post(f"/api/admin/users/{alice.pk}/block/", {"reason": "x", "password": password}, format="json").status_code == 200
    # Без 2FA в сесії — ні
    assert client_for(staff).post(f"/api/admin/users/{alice.pk}/unblock/", {}, format="json").status_code == 403
    # Старий PATCH is_active теж фіксує блокування й завершує сесії
    bob = make_user("bob2")
    r = client_for(admin, mfa=True).patch(f"/api/admin/users/{bob.pk}/", {"is_active": False}, format="json")
    assert r.status_code == 200
    bob.refresh_from_db()
    assert bob.blocked_at is not None


# ═══════════════════ Видалення скриньки ═══════════════════


class FakeImap:
    def __init__(self, folders):
        # {назва: кількість листів}
        self.folders = dict(folders)
        self.selected = None
        self.deleted = []

    def list(self):
        rows = []
        for name in self.folders:
            flags = "\\HasNoChildren"
            rows.append(f'({flags}) "/" "{name}"'.encode())
        rows.append(b'(\\Noselect \\HasChildren) "/" "Projects"')
        return "OK", rows

    def select(self, name, readonly=True):
        name = name.strip('"')
        if name not in self.folders:
            return "NO", [b"no"]
        self.selected = name
        return "OK", [str(self.folders[name]).encode()]

    def uid(self, cmd, *args):
        assert cmd == "STORE" and args[0] == "1:*" and "Deleted" in args[2]
        self._flagged = True
        return "OK", [None]

    def expunge(self):
        self.folders[self.selected] = 0
        return "OK", [None]

    def close(self):
        return "OK", [None]

    def unsubscribe(self, name):
        return "OK", [None]

    def delete(self, name):
        name = name.strip('"')
        self.deleted.append(name)
        self.folders.pop(name, None)
        return "OK", [None]


@pytest.fixture
def fake_imap(monkeypatch):
    from apps.mail import imap

    conn = FakeImap({"INBOX": 5, "Sent": 2, "Projects/2026": 1})

    @contextmanager
    def session(address):
        yield conn

    monkeypatch.setattr(imap, "imap_session", session)
    return conn


@pytest.mark.django_db
def test_admin_deletes_mailbox(alice, admin, client_for, password, fake_imap):
    mb = _mailbox(alice, "sales")
    Alias.objects.create(source="old-sales@example.com", destination=mb.address)
    Alias.objects.create(source=mb.address, destination="alice@example.com")
    keep = Alias.objects.create(source="other@example.com", destination="alice@example.com")
    c = client_for(admin, mfa=True)
    url = f"/api/admin/mailboxes/{mb.pk}/"
    assert c.delete(url, {"password": password, "confirm": "wrong@example.com"}, format="json").status_code == 400
    assert c.delete(url, {"password": "bad-password", "confirm": mb.address}, format="json").status_code == 400
    assert Mailbox.objects.filter(pk=mb.pk).exists()
    r = c.delete(url, {"password": password, "confirm": mb.address.upper()}, format="json")
    assert r.status_code == 204, r.content
    assert not Mailbox.objects.filter(pk=mb.pk).exists()
    assert list(Alias.objects.values_list("pk", flat=True)) == [keep.pk]
    assert fake_imap.folders == {"INBOX": 0}
    assert "Projects/2026" in fake_imap.deleted and "Projects" in fake_imap.deleted
    assert fake_imap.deleted.index("Projects/2026") < fake_imap.deleted.index("Projects")
    assert User.objects.filter(pk=alice.pk, is_active=True).exists()
    from apps.core.models import AuditLog, Notification

    assert AuditLog.objects.filter(action="admin.mailbox_deleted", target=mb.address).exists()
    assert Notification.objects.filter(user=alice).exists()


@pytest.mark.django_db
def test_mailbox_delete_refuses_without_imap(alice, admin, staff, client_for, password, monkeypatch):
    from apps.mail import imap

    @contextmanager
    def broken(address):
        raise imap.MailUnavailable()
        yield

    monkeypatch.setattr(imap, "imap_session", broken)
    mb = _mailbox(alice, "team")
    mb.active = False
    mb.save()
    body = {"password": password, "confirm": mb.address}
    assert client_for(admin, mfa=True).delete(f"/api/admin/mailboxes/{mb.pk}/", body, format="json").status_code == 503
    mb.refresh_from_db()
    assert mb.active is False  # тимчасове ввімкнення відкочено
    # force — лише суперкористувач
    assert client_for(staff, mfa=True).delete(f"/api/admin/mailboxes/{mb.pk}/", {**body, "force": True}, format="json").status_code == 403
    r = client_for(admin, mfa=True).delete(f"/api/admin/mailboxes/{mb.pk}/", {**body, "force": True}, format="json")
    assert r.status_code == 204
    assert not Mailbox.objects.filter(pk=mb.pk).exists()


@pytest.mark.django_db
def test_mailbox_delete_permissions(alice, admin, staff, client_for, password, fake_imap):
    reserved = _mailbox(alice, "postmaster")
    admin_box = _mailbox(admin, "chief-box")
    c = client_for(staff, mfa=True)
    assert c.delete(f"/api/admin/mailboxes/{reserved.pk}/", {"password": password, "confirm": reserved.address}, format="json").status_code == 403
    assert c.delete(f"/api/admin/mailboxes/{admin_box.pk}/", {"password": password, "confirm": admin_box.address}, format="json").status_code == 403
    assert client_for(alice).delete(f"/api/admin/mailboxes/{reserved.pk}/", {"password": password, "confirm": reserved.address}, format="json").status_code == 403
    assert Mailbox.objects.filter(pk__in=[reserved.pk, admin_box.pk]).count() == 2
    r = client_for(admin, mfa=True).delete(f"/api/admin/mailboxes/{reserved.pk}/", {"password": password, "confirm": reserved.address}, format="json")
    assert r.status_code == 204
