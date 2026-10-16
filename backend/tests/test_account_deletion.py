"""Видалення акаунта адміністратором: блок одразу, стирання — через ACCOUNT_DELETION_DELAY_DAYS."""

from datetime import timedelta

import pytest
from django.conf import settings as dj_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import deletion
from apps.accounts.models import AccountDeletion, User, UserSession
from apps.core.models import AuditLog
from apps.mail import imap
from apps.mail import views_admin as mail_admin
from apps.mail.models import Mailbox
from apps.storage.models import File

from .test_admin_batch5 import admin, staff  # noqa: F401 — фікстури
from .test_storage import upload


def _url(user, action="schedule_delete"):
    return f"/api/admin/users/{user.pk}/{action}/"


def _schedule(c, user, password, **extra):
    return c.post(_url(user), {"password": password, "confirm": user.username, **extra}, format="json")


def _make_due(user):
    AccountDeletion.objects.filter(user=user).update(purge_after=timezone.now() - timedelta(minutes=1))


@pytest.fixture
def wiped(monkeypatch, settings):
    """IMAP замоканий: записуємо, які скриньки стерто."""
    settings.DOVECOT_MASTER_PASSWORD = "master-for-tests"
    calls = []

    def fake_wipe(address):
        calls.append(address)
        return 3

    monkeypatch.setattr(mail_admin, "wipe_mailbox", fake_wipe)
    return calls


@pytest.mark.django_db
def test_schedule_blocks_now_and_purges_later(alice, admin, client_for, password, wiped):  # noqa: F811
    ac = APIClient()
    assert ac.post("/api/auth/login/", {"login": "alice", "password": password}, format="json").status_code == 200
    info = upload(client_for(alice), b"secret data" * 100, "a.txt")
    f = File.objects.get(pk=info["id"])
    blob_dir = dj_settings.LOCAL_OBJECT_STORE_DIR / f.object_prefix
    assert blob_dir.exists()
    assert Mailbox.objects.filter(user=alice).exists()

    c = client_for(admin, mfa=True)
    assert c.post(_url(alice), {"password": password, "confirm": "wrong"}, format="json").status_code == 400
    assert c.post(_url(alice), {"password": "bad-password", "confirm": "alice"}, format="json").status_code == 400
    r = _schedule(c, alice, password, reason="Звільнився")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["is_active"] is False and body["deletion"]["requested_by"] == "chief"
    alice.refresh_from_db()
    assert not alice.is_active and alice.blocked_reason == "Звільнився"
    assert not UserSession.objects.filter(user=alice).exists()
    d = AccountDeletion.objects.get(user=alice)
    delay = (d.purge_after - timezone.now()).total_seconds() / 86400
    assert 6.9 < delay <= 7
    assert d.addresses == ["alice@example.com"]
    assert AuditLog.objects.filter(action="admin.user_delete_scheduled", target="alice").exists()
    # Повторно — ні
    assert _schedule(c, alice, password).status_code == 400

    # Термін ще не минув — нічого не відбувається
    assert deletion.purge_due() == 0
    assert User.objects.filter(pk=alice.pk).exists() and blob_dir.exists()

    _make_due(alice)
    assert deletion.purge_due() == 1
    assert not User.objects.filter(username="alice").exists()
    assert not File.objects.filter(pk=f.pk).exists() and not blob_dir.exists()
    assert not Mailbox.objects.filter(local_part="alice").exists()
    assert wiped == ["alice@example.com"]
    d.refresh_from_db()
    assert d.user is None and d.purged_at and d.started_at is None
    assert AuditLog.objects.filter(action="account.purged", target="alice").exists()


@pytest.mark.django_db
def test_other_users_untouched(alice, make_user, admin, client_for, password, wiped):  # noqa: F811
    bob = make_user("bob")
    bob_file = upload(client_for(bob), b"bob's", "b.txt")
    _schedule(client_for(admin, mfa=True), alice, password)
    _make_due(alice)
    deletion.purge_due()
    assert File.objects.filter(pk=bob_file["id"]).exists()
    assert Mailbox.objects.filter(user=bob).exists()
    assert User.objects.get(pk=bob.pk).is_active


@pytest.mark.django_db
def test_mail_unavailable_postpones_purge(alice, admin, client_for, password, monkeypatch, settings):  # noqa: F811
    settings.DOVECOT_MASTER_PASSWORD = "master-for-tests"

    def broken(address):
        raise imap.MailUnavailable()

    monkeypatch.setattr(mail_admin, "wipe_mailbox", broken)
    Mailbox.objects.filter(user=alice).update(client_password_hash="{ARGON2ID}x")
    _schedule(client_for(admin, mfa=True), alice, password)
    _make_due(alice)
    assert deletion.purge_due() == 0
    assert User.objects.filter(pk=alice.pk).exists()
    d = AccountDeletion.objects.get(user=alice)
    assert d.started_at is None and d.last_error == "mail_unavailable" and d.attempts == 1
    mb = Mailbox.objects.get(user=alice)
    assert mb.client_password_hash == "{ARGON2ID}x"  # нічого не зіпсовано — повтор за годину


@pytest.mark.django_db
def test_cancel_and_unblock_rules(alice, admin, client_for, password):  # noqa: F811
    c = client_for(admin, mfa=True)
    _schedule(c, alice, password)
    # Поки заплановано видалення — розблокувати не можна
    assert c.post(_url(alice, "unblock"), {}, format="json").status_code == 400
    assert c.patch(f"/api/admin/users/{alice.pk}/", {"is_active": True}, format="json").status_code == 400
    r = c.post(_url(alice, "cancel_delete"), {}, format="json")
    assert r.status_code == 200 and r.json()["deletion"] is None and r.json()["is_active"] is False
    assert c.post(_url(alice, "cancel_delete"), {}, format="json").status_code == 400
    assert c.post(_url(alice, "unblock"), {}, format="json").status_code == 200
    assert AuditLog.objects.filter(action="admin.user_delete_cancelled").exists()
    _make_due(alice)
    assert deletion.purge_due() == 0 and User.objects.get(pk=alice.pk).is_active


@pytest.mark.django_db
def test_delete_permissions(alice, admin, staff, make_user, client_for, password):  # noqa: F811
    c = client_for(staff, mfa=True)
    assert _schedule(c, staff, password).status_code == 400  # себе — ні
    assert _schedule(c, admin, password).status_code == 403  # суперкористувача — ні
    other_admin = make_user("other", is_staff=True)
    assert _schedule(c, other_admin, password).status_code == 403
    assert _schedule(client_for(staff), alice, password).status_code == 403  # без 2FA в сесії
    assert _schedule(client_for(make_user("bob")), alice, password).status_code == 403
    assert _schedule(c, alice, password).status_code == 200


@pytest.mark.django_db
def test_name_and_address_on_hold(alice, admin, client_for, password, wiped, settings):  # noqa: F811
    c = client_for(admin, mfa=True)
    _schedule(c, alice, password)
    _make_due(alice)
    deletion.purge_due()
    new = {"username": "alice", "email": "new-alice@example.org", "password": password}
    r = c.post("/api/admin/users/", new, format="json")
    assert r.status_code == 400 and "username" in r.json()
    assert deletion.address_on_hold("ALICE@example.com")
    # Через ACCOUNT_NAME_HOLD_DAYS — знову вільне
    AccountDeletion.objects.update(purged_at=timezone.now() - timedelta(days=settings.ACCOUNT_NAME_HOLD_DAYS + 1))
    assert not deletion.address_on_hold("alice@example.com")
    assert c.post("/api/admin/users/", new, format="json").status_code == 201


@pytest.mark.django_db
def test_shared_folder_contents_go_with_owner(alice, make_user, admin, client_for, password, wiped):  # noqa: F811
    from apps.storage.models import Folder

    folder = Folder.objects.create(owner=alice, name="Shared")
    sub = Folder.objects.create(owner=alice, parent=folder, name="Sub")
    info = upload(client_for(alice), b"x" * 50, "in-sub.txt", folder=str(sub.pk))
    _schedule(client_for(admin, mfa=True), alice, password)
    _make_due(alice)
    assert deletion.purge_due() == 1
    assert not Folder.objects.filter(pk__in=[folder.pk, sub.pk]).exists()
    assert not File.objects.filter(pk=info["id"]).exists()
