"""Тести: акцентний колір, карантин/антивірус, кілька скриньок, підписи,
HTML-листи з вбудованими зображеннями, конфіденційний режим."""

import base64
import email
from email.policy import default as default_policy

import pytest
from rest_framework.test import APIClient

from apps.mail.models import Alias, ConfidentialMessage, Mailbox, Signature
from apps.storage.models import File

from .test_accounts import _enable_totp
from .test_storage import upload

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.fixture
def sanitizer(monkeypatch):
    """Справжній nh3 (Rust) недоступний в офлайн-середовищі тестів — тоді санітизатор
    підміняється тотожним. У CI з установленим nh3 використовується справжній."""
    import nh3

    from apps.mail import sanitize

    try:
        nh3.clean("<b>x</b>")
    except NotImplementedError:
        monkeypatch.setattr(sanitize, "sanitize_outgoing_html", lambda html: html)
        monkeypatch.setattr(sanitize, "sanitize_html", lambda html, **kw: html)
    return sanitize


@pytest.fixture
def admin(make_user, settings):
    settings.REQUIRE_2FA_FOR_STAFF = True
    u = make_user("chief", is_staff=True, is_superuser=True)
    _enable_totp(u)
    return u


# ═══════════════════ Тема й акцентний колір ═══════════════════


@pytest.mark.django_db
def test_accent_preference_saved_and_validated(alice, client_for):
    c = client_for(alice)
    r = c.patch("/api/account/preferences/", {"accent": "teal", "theme": "light"}, format="json")
    assert r.status_code == 200 and r.json()["accent"] == "teal"
    assert c.get("/api/auth/me/").json()["user"]["preferences"]["accent"] == "teal"
    assert c.patch("/api/account/preferences/", {"accent": "neon"}, format="json").status_code == 400


# ═══════════════════ Карантин і антивірус ═══════════════════


def _infect(f_id, detail="Eicar-Test-Signature"):
    File.objects.filter(pk=f_id).update(status=File.Status.INFECTED, scan_detail=detail)


@pytest.mark.django_db
def test_quarantine_requires_admin_with_mfa(alice, admin, client_for):
    info = upload(client_for(alice), b"X5O!P%@AP", name="eicar.com")
    _infect(info["id"])
    assert client_for(alice).get("/api/admin/quarantine/").status_code == 403
    assert client_for(admin).get("/api/admin/quarantine/").status_code == 403  # без MFA-сесії
    items = client_for(admin, mfa=True).get("/api/admin/quarantine/").json()
    assert [i["id"] for i in items] == [info["id"]]
    assert items[0]["scan_detail"] == "Eicar-Test-Signature"
    # заражений файл і надалі недоступний власнику
    assert client_for(alice).get(f"/api/files/items/{info['id']}/download/").status_code in (403, 404)


@pytest.mark.django_db
def test_quarantine_rescan_goes_through_antivirus(alice, admin, client_for, settings, monkeypatch):
    from apps.storage import tasks

    info = upload(client_for(alice), b"suspicious", name="x.bin")
    _infect(info["id"])
    queued = []
    monkeypatch.setattr(tasks.scan_file, "delay", lambda fid: queued.append(fid))
    c = client_for(admin, mfa=True)
    settings.CLAMAV_ENABLED = False
    assert c.post(f"/api/admin/quarantine/{info['id']}/rescan/").status_code == 400
    settings.CLAMAV_ENABLED = True
    assert c.post(f"/api/admin/quarantine/{info['id']}/rescan/").status_code == 200
    assert queued == [info["id"]]
    assert File.objects.get(pk=info["id"]).status == File.Status.SCANNING  # не «чистий» автоматично
    # файл, що не в карантині, перевірити повторно не можна
    clean = upload(client_for(alice), b"fine", name="ok.txt")
    assert c.post(f"/api/admin/quarantine/{clean['id']}/rescan/").status_code == 404


@pytest.mark.django_db
def test_quarantine_purge_needs_password_and_frees_quota(alice, admin, client_for, password):
    from apps.core.models import Notification

    info = upload(client_for(alice), b"bad bytes", name="bad.exe")
    _infect(info["id"])
    alice.refresh_from_db()
    used = alice.used_bytes
    c = client_for(admin, mfa=True)
    assert c.post(f"/api/admin/quarantine/{info['id']}/purge/", {"password": "wrong"}, format="json").status_code == 400
    assert File.objects.filter(pk=info["id"]).exists()
    r = c.post(f"/api/admin/quarantine/{info['id']}/purge/", {"password": password}, format="json")
    assert r.status_code == 204
    assert not File.objects.filter(pk=info["id"]).exists()
    alice.refresh_from_db()
    assert alice.used_bytes == used - len(b"bad bytes")
    assert Notification.objects.filter(user=alice, title__icontains="карантину").exists()


@pytest.mark.django_db
def test_staff_cannot_purge_other_admins_quarantine(make_user, client_for, password, settings):
    settings.REQUIRE_2FA_FOR_STAFF = True
    helper = make_user("helper", is_staff=True)
    other = make_user("helper2", is_staff=True)
    for u in (helper, other):
        _enable_totp(u)
    info = upload(client_for(other), b"bad", name="b.bin")
    _infect(info["id"])
    r = client_for(helper, mfa=True).post(f"/api/admin/quarantine/{info['id']}/purge/", {"password": password}, format="json")
    assert r.status_code == 403


@pytest.mark.django_db
def test_antivirus_status(admin, client_for, settings, monkeypatch):
    from apps.storage import scanner

    settings.CLAMAV_ENABLED = True
    monkeypatch.setattr(scanner, "_command", lambda cmd, timeout=5.0: "PONG" if cmd == b"PING" else "ClamAV 1.0.7/27420/x")
    data = client_for(admin, mfa=True).get("/api/admin/antivirus/").json()
    assert data["reachable"] is True and data["version"].startswith("ClamAV")
    assert "infected" in data["counts"]


# ═══════════════════ Кілька поштових скриньок ═══════════════════


@pytest.mark.django_db
def test_admin_creates_and_renames_mailboxes(alice, admin, client_for, settings):
    c = client_for(admin, mfa=True)
    assert client_for(alice).post("/api/admin/mailboxes/", {}, format="json").status_code == 403
    r = c.post("/api/admin/mailboxes/", {"username": "alice", "local_part": "sales", "display_name": "Продажі"}, format="json")
    assert r.status_code == 201, r.content
    box = r.json()
    assert box["address"] == f"sales@{settings.MAIL_DOMAIN}"
    assert Mailbox.objects.filter(user=alice).count() == 2
    # зайнята адреса (і як скринька, і як псевдонім)
    assert c.post("/api/admin/mailboxes/", {"username": "alice", "local_part": "sales"}, format="json").status_code == 400
    Alias.objects.create(source=f"info@{settings.MAIL_DOMAIN}", destination=box["address"])
    assert c.post("/api/admin/mailboxes/", {"username": "alice", "local_part": "info"}, format="json").status_code == 400

    mb = Mailbox.objects.get(pk=box["id"])
    maildir = mb.maildir
    r = c.patch(f"/api/admin/mailboxes/{box['id']}/", {"local_part": "office", "display_name": "Офіс"}, format="json")
    assert r.status_code == 200, r.content
    mb.refresh_from_db()
    assert mb.address == f"office@{settings.MAIL_DOMAIN}" and mb.display_name == "Офіс"
    assert mb.maildir == maildir  # листи не переміщуються
    assert Alias.objects.get(source=f"sales@{settings.MAIL_DOMAIN}").destination == mb.address
    assert Alias.objects.get(source=f"info@{settings.MAIL_DOMAIN}").destination == mb.address


@pytest.mark.django_db
def test_new_mailbox_never_reuses_renamed_maildir(alice, admin, client_for):
    c = client_for(admin, mfa=True)
    first = Mailbox.objects.get(user=alice)
    old_dir = first.maildir
    c.patch(f"/api/admin/mailboxes/{first.pk}/", {"local_part": "alice2", "keep_old_alias": False}, format="json")
    r = c.post("/api/admin/mailboxes/", {"username": "alice", "local_part": "alice"}, format="json")
    assert r.status_code == 201
    assert Mailbox.objects.get(pk=r.json()["id"]).maildir != old_dir


@pytest.mark.django_db
def test_staff_cannot_manage_other_admins_mailboxes(make_user, client_for, settings):
    settings.REQUIRE_2FA_FOR_STAFF = True
    helper = make_user("helper", is_staff=True)
    boss = make_user("boss", is_staff=True)
    _enable_totp(helper)
    c = client_for(helper, mfa=True)
    assert c.post("/api/admin/mailboxes/", {"username": "boss", "local_part": "boss2"}, format="json").status_code == 403
    mb = Mailbox.objects.get(user=boss)
    assert c.patch(f"/api/admin/mailboxes/{mb.pk}/", {"active": False}, format="json").status_code == 403


@pytest.mark.django_db
def test_webmail_mailbox_selection_is_owner_only(alice, bob, admin, client_for):
    client_for(admin, mfa=True).post("/api/admin/mailboxes/", {"username": "alice", "local_part": "team"}, format="json")
    boxes = client_for(alice).get("/api/mail/mailboxes/").json()
    assert len(boxes) == 2
    second = boxes[1]["id"]
    assert client_for(alice).get(f"/api/mail/mailbox/?mailbox={second}").json()["address"].startswith("team@")
    assert client_for(bob).get(f"/api/mail/mailbox/?mailbox={second}").status_code == 404
    assert client_for(bob).get("/api/mail/mailbox/?mailbox=not-a-uuid").status_code == 404


# ═══════════════════ Підписи ═══════════════════


@pytest.mark.django_db
def test_signatures_crud_single_default(alice, bob, client_for, sanitizer):
    c = client_for(alice)
    a = c.post("/api/mail/signatures/", {"name": "Робочий", "html": "<b>Yurii</b>", "is_default": True}, format="json").json()
    b = c.post("/api/mail/signatures/", {"name": "Короткий", "html": "Y.", "is_default": True}, format="json").json()
    sigs = {s["id"]: s for s in c.get("/api/mail/signatures/").json()}
    assert sigs[b["id"]]["is_default"] and not sigs[a["id"]]["is_default"]
    assert client_for(bob).patch(f"/api/mail/signatures/{a['id']}/", {"name": "x"}, format="json").status_code == 404
    assert c.delete(f"/api/mail/signatures/{a['id']}/").status_code == 204
    assert Signature.objects.filter(user=alice).count() == 1


# ═══════════════════ HTML-листи ═══════════════════


def _build(user, data, attachments=()):
    from apps.mail.imap import build_message

    mb = Mailbox.objects.select_related("domain").get(user=user)
    return build_message(user, mb, data, list(attachments))


@pytest.mark.django_db
def test_html_message_inline_images_become_cid_parts(alice, sanitizer):
    img = "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()
    html = f'<p style="font-size:18px"><b>Привіт</b> <a href="https://example.org">лінк</a></p><img src="{img}" alt="logo">'
    msg, rcpts, _ = _build(alice, {"to": "bob@example.org", "subject": "Hi", "html": html})
    assert rcpts == ["bob@example.org"]
    parsed = email.message_from_bytes(msg.as_bytes(), policy=default_policy)
    html_part = parsed.get_body(preferencelist=("html",))
    text_part = parsed.get_body(preferencelist=("plain",))
    assert "cid:" in html_part.get_content() and "data:image" not in html_part.get_content()
    assert "Привіт" in text_part.get_content() and "https://example.org" in text_part.get_content()
    images = [p for p in parsed.walk() if p.get_content_type() == "image/png"]
    assert len(images) == 1 and images[0].get_payload(decode=True) == PNG_1PX
    cid = images[0]["Content-ID"].strip("<>")
    assert f"cid:{cid}" in html_part.get_content()


@pytest.mark.django_db
def test_plain_message_still_works(alice):
    msg, _, _ = _build(alice, {"to": "bob@example.org", "subject": "T", "body": "just text"})
    assert msg.get_content_type() == "text/plain"


def test_html_to_text_and_bad_image():
    from apps.mail.sanitize import extract_data_images, html_to_text

    assert html_to_text("<p>A</p><ul><li>one</li></ul><a href='mailto:x@y.z'>mail</a>").splitlines()[0] == "A"
    with pytest.raises(ValueError):
        extract_data_images('<img src="data:image/png;base64,AAA">', "example.org")


# ═══════════════════ Конфіденційний режим ═══════════════════


@pytest.mark.django_db
def test_confidential_message_flow(alice, client_for, sanitizer):
    msg, _, extra = _build(
        alice,
        {
            "to": "bob@example.org",
            "subject": "Таємно",
            "html": "<p>секретний вміст</p>",
            "confidential": "true",
            "confidential_days": "7",
            "confidential_passcode": "true",
        },
    )
    parsed = email.message_from_bytes(msg.as_bytes(), policy=default_policy)
    plain = parsed.get_body(("plain",)).get_content()
    assert "секретний" not in plain and "секретний" not in parsed.get_body(("html",)).get_content()
    assert "/c#" in plain
    passcode = extra["confidential"]["passcode"]
    assert len(passcode) == 6
    record = ConfidentialMessage.objects.get(pk=extra["confidential"]["id"])
    assert "секретний" not in record.html_encrypted  # зашифровано

    token = [w for w in plain.split() if "/c#" in w][0].split("#", 1)[1]
    anon = APIClient()
    assert anon.post("/api/public/confidential/open/", {"token": token}, format="json").json()["requires_passcode"] is True
    assert anon.post("/api/public/confidential/open/", {"token": token, "passcode": "000000" if passcode != "000000" else "111111"}, format="json").status_code == 403
    r = anon.post("/api/public/confidential/open/", {"token": token, "passcode": passcode}, format="json").json()
    page = anon.get(r["render_url"])
    assert page.status_code == 200 and "секретний вміст" in page.content.decode()
    assert "sandbox" in page["Content-Security-Policy"]

    # відкликання відправником
    assert client_for(alice).post(f"/api/mail/confidential/{record.pk}/revoke/").status_code == 200
    assert anon.post("/api/public/confidential/open/", {"token": token, "passcode": passcode}, format="json").status_code == 404
    assert anon.get(r["render_url"]).status_code == 404


@pytest.mark.django_db
def test_confidential_rejects_attachments_and_bad_terms(alice, sanitizer):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from rest_framework.exceptions import ValidationError

    with pytest.raises(ValidationError):
        _build(alice, {"to": "b@example.org", "html": "<p>x</p>", "confidential": "1"}, [SimpleUploadedFile("a.txt", b"a")])
    with pytest.raises(ValidationError):
        _build(alice, {"to": "b@example.org", "html": "<p>x</p>", "confidential": "1", "confidential_days": "3"})


@pytest.mark.django_db
def test_confidential_expiry_purges_content(alice, sanitizer):
    from datetime import timedelta

    from django.utils import timezone

    from apps.mail.confidential import purge_expired

    _, _, extra = _build(alice, {"to": "b@example.org", "html": "<p>x</p>", "confidential": "1", "confidential_days": "1"})
    ConfidentialMessage.objects.filter(pk=extra["confidential"]["id"]).update(expires_at=timezone.now() - timedelta(minutes=1))
    assert purge_expired() == 1
    assert ConfidentialMessage.objects.get(pk=extra["confidential"]["id"]).html_encrypted == ""


def test_sanitize_outgoing_html_real_nh3():
    """Перевіряє справжню санітизацію вихідного HTML (виконується там, де встановлено nh3, — у CI)."""
    import nh3

    try:
        nh3.clean("<b>x</b>")
    except NotImplementedError:
        pytest.skip("nh3 недоступний в офлайн-середовищі")
    from apps.mail.sanitize import sanitize_outgoing_html

    img = "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()
    out = sanitize_outgoing_html(
        f'<p style="font-size:18px" onclick="x()"><b>ok</b><script>alert(1)</script>'
        f'<a href="javascript:alert(1)">bad</a><a href="https://example.org">good</a>'
        f'<img src="{img}"><img src="data:image/svg+xml;base64,PHN2Zz4="><img src="http://insecure/x.png"></p>'
    )
    assert "<b>ok</b>" in out and "font-size:18px" in out
    assert "script" not in out and "onclick" not in out and "javascript" not in out
    assert "https://example.org" in out and img in out
    assert "svg" not in out and "http://insecure" not in out
