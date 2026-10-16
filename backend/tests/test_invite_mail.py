"""Запрошення на email: лист із посиланням, повторне надсилання з новим токеном."""

import email
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts import invite_mail
from apps.accounts.models import Invite
from apps.core.models import AuditLog

from .test_admin_batch5 import admin  # noqa: F401 — фікстура

URL = "/api/admin/invites/"


class FakeSMTP:
    sent: list = []
    fail: Exception | None = None

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def sendmail(self, sender, rcpts, raw):
        if FakeSMTP.fail:
            raise FakeSMTP.fail
        FakeSMTP.sent.append((sender, rcpts, raw))


@pytest.fixture
def smtp(monkeypatch, settings):
    settings.DOMAIN = "cloud.example.com"
    settings.MAIL_DOMAIN = "example.com"
    settings.SYSTEM_MAIL_FROM = "noreply@example.com"
    settings.SITE_NAME = "Brebston Cloud"
    FakeSMTP.sent = []
    FakeSMTP.fail = None
    monkeypatch.setattr(invite_mail.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def _text(raw: bytes) -> str:
    msg = email.message_from_bytes(raw)
    return "\n".join(p.get_payload(decode=True).decode() for p in msg.walk() if p.get_content_maintype() == "text")


@pytest.mark.django_db
def test_invite_is_emailed(admin, client_for, smtp):  # noqa: F811
    c = client_for(admin, mfa=True)
    r = c.post(URL, {"email": "Friend@Example.org", "days": 3}, format="json")
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["email"] == "friend@example.org" and body["send_count"] == 1 and body["sent_at"] and body["send_error"] == ""
    token = body["token"]
    assert body["url"] == f"https://cloud.example.com/register?invite={token}"
    sender, rcpts, raw = smtp.sent[0]
    assert sender == "noreply@example.com" and rcpts == ["friend@example.org"]
    msg = email.message_from_bytes(raw)
    assert "Brebston Cloud" in str(email.header.make_header(email.header.decode_header(msg["Subject"])))
    assert msg["Auto-Submitted"] == "auto-generated"
    text = _text(raw)
    assert body["url"] in text and "friend@example.org" in text
    inv = Invite.objects.get(pk=body["id"])
    assert 2.9 < (inv.expires_at - timezone.now()).total_seconds() / 86400 <= 3
    # У БД — лише хеш токена
    assert token not in inv.token_hash and inv.token_hash == Invite.hash_token(token)
    assert AuditLog.objects.filter(action="admin.invite_sent").exists()


@pytest.mark.django_db
def test_invite_without_email_or_opt_out_is_not_sent(admin, client_for, smtp):  # noqa: F811
    c = client_for(admin, mfa=True)
    assert c.post(URL, {"days": 7}, format="json").json()["send_count"] == 0
    assert c.post(URL, {"email": "x@example.org", "send_email": False}, format="json").json()["send_count"] == 0
    assert smtp.sent == []


@pytest.mark.django_db
def test_send_failure_keeps_invite_and_link(admin, client_for, smtp):  # noqa: F811
    smtp.fail = OSError("down")
    r = client_for(admin, mfa=True).post(URL, {"email": "x@example.org"}, format="json")
    assert r.status_code == 201
    body = r.json()
    assert body["send_error"] == "smtp_unavailable" and body["send_count"] == 0 and body["url"]
    assert AuditLog.objects.filter(action="admin.invite_send_failed").exists()


@pytest.mark.django_db
def test_resend_rotates_token(admin, client_for, smtp, alice):  # noqa: F811
    c = client_for(admin, mfa=True)
    first = c.post(URL, {"email": "x@example.org", "days": 1}, format="json").json()
    r = c.post(f"{URL}{first['id']}/resend/", {}, format="json")
    assert r.status_code == 200
    second = r.json()
    assert second["token"] != first["token"] and second["send_count"] == 2
    inv = Invite.objects.get(pk=first["id"])
    assert inv.token_hash == Invite.hash_token(second["token"])  # старе посилання більше не діє
    # Прострочене — лише з новим терміном
    Invite.objects.filter(pk=inv.pk).update(expires_at=timezone.now() - timedelta(hours=1))
    assert c.post(f"{URL}{inv.pk}/resend/", {}, format="json").status_code == 400
    r = c.post(f"{URL}{inv.pk}/resend/", {"days": 14}, format="json")
    assert r.status_code == 200 and r.json()["valid"] is True
    # Без email або вже використане — ні
    anon = c.post(URL, {"days": 2}, format="json").json()
    assert c.post(f"{URL}{anon['id']}/resend/", {}, format="json").status_code == 400
    Invite.objects.filter(pk=inv.pk).update(used_at=timezone.now(), used_by=alice)
    assert c.post(f"{URL}{inv.pk}/resend/", {"days": 3}, format="json").status_code == 400


@pytest.mark.django_db
def test_existing_user_email_refused(admin, client_for, smtp, alice):  # noqa: F811
    r = client_for(admin, mfa=True).post(URL, {"email": alice.email}, format="json")
    assert r.status_code == 400 and "email" in r.json()
    assert smtp.sent == []


@pytest.mark.django_db
def test_emailed_link_registers(admin, client_for, smtp, password):  # noqa: F811
    from rest_framework.test import APIClient

    token = client_for(admin, mfa=True).post(URL, {"email": "new@example.org"}, format="json").json()["token"]
    r = APIClient().post(
        "/api/auth/register/",
        {"username": "newbie", "email": "new@example.org", "password": password, "invite": token},
        format="json",
    )
    assert r.status_code in (200, 201), r.content
    assert Invite.objects.get(token_hash=Invite.hash_token(token)).used_at is not None
