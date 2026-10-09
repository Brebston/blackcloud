"""Тести: чернетки з автозбереженням (IMAP «Чернетки») і відкладене надсилання."""

import email
import re
from datetime import timedelta
from email.policy import default as default_policy

import pytest
from django.utils import timezone

from apps.mail import imap
from apps.mail.models import ConfidentialMessage, ScheduledMessage

from .test_features_batch import sanitizer  # noqa: F401  (фікстура)


class FakeIMAP:
    """Мінімальний IMAP-сервер у пам'яті: теки, APPEND з APPENDUID, SELECT, UID FETCH/STORE/EXPUNGE/SEARCH."""

    store: dict = {}

    def __init__(self):
        self.selected = None

    @classmethod
    def reset(cls):
        cls.store = {"INBOX": {}, "Drafts": {}, "Sent": {}}
        cls.next_uid = 1

    def login(self, *a):
        return "OK", [b""]

    def logout(self):
        return "BYE", [b""]

    def list(self):
        return "OK", [b'(\\HasNoChildren \\Drafts) "/" "Drafts"', b'(\\HasNoChildren \\Sent) "/" "Sent"', b'(\\HasNoChildren) "/" "INBOX"']

    def select(self, name, readonly=False):
        name = name.strip('"')
        if name not in self.store:
            return "NO", [b""]
        self.selected = name
        return "OK", [str(len(self.store[name])).encode()]

    def append(self, folder, flags, date, raw):
        folder = folder.strip('"')
        uid = FakeIMAP.next_uid
        FakeIMAP.next_uid += 1
        self.store[folder][uid] = {"raw": raw, "flags": set(flags.strip("()").split())}
        return "OK", [f"[APPENDUID 1 {uid}] Append completed.".encode()]

    def uid(self, cmd, *args):
        box = self.store[self.selected]
        if cmd == "FETCH":
            item = box.get(int(args[0]))
            return "OK", ([(b"1 (UID BODY[] {1}", item["raw"]), b")"] if item else [None])
        if cmd == "STORE":
            item = box.get(int(args[0]))
            if item:
                item["flags"].add(args[2].strip("()"))
            return "OK", [b""]
        if cmd == "EXPUNGE":
            uid = int(args[0])
            if uid in box and "\\Deleted" in box[uid]["flags"]:
                del box[uid]
            return "OK", [b""]
        if cmd == "SEARCH":
            wanted = args[-1].strip('"')
            hits = [str(u) for u, v in box.items() if wanted.encode() in v["raw"]]
            return "OK", [" ".join(hits).encode()]
        raise AssertionError(cmd)


class FakeSMTP:
    sent: list = []
    fail = False

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def sendmail(self, from_addr, to_addrs, raw):
        if FakeSMTP.fail:
            raise OSError("down")
        FakeSMTP.sent.append((from_addr, list(to_addrs), raw))


@pytest.fixture
def mailserver(monkeypatch, settings):
    from contextlib import contextmanager

    settings.DOVECOT_MASTER_PASSWORD = "test"
    FakeIMAP.reset()
    FakeSMTP.sent = []
    FakeSMTP.fail = False

    @contextmanager
    def session(address):
        yield FakeIMAP()

    monkeypatch.setattr(imap, "imap_session", session)
    monkeypatch.setattr(imap.smtplib, "SMTP", FakeSMTP)
    return FakeIMAP


# ═══════════════════ Чернетки ═══════════════════


@pytest.mark.django_db
def test_draft_autosave_replaces_previous_version(alice, client_for, mailserver, sanitizer):
    c = client_for(alice)
    r1 = c.post("/api/mail/drafts/", {"to": "", "subject": "Чернетка", "html": "<p>перша</p>"}, format="json")
    assert r1.status_code == 201, r1.content
    uid1 = r1.json()["uid"]
    r2 = c.post(
        "/api/mail/drafts/",
        {"draft_uid": uid1, "to": "bob@example.org", "bcc": "boss@example.org", "subject": "Чернетка", "html": "<p>друга</p>"},
        format="json",
    )
    uid2 = r2.json()["uid"]
    assert uid2 != uid1
    assert list(mailserver.store["Drafts"]) == [uid2]  # попередня версія прибрана

    d = c.get(f"/api/mail/drafts/{uid2}/").json()
    assert d["to"] == "bob@example.org" and d["bcc"] == "boss@example.org" and "друга" in d["html"]

    assert c.delete(f"/api/mail/drafts/{uid2}/").status_code == 204
    assert mailserver.store["Drafts"] == {}


@pytest.mark.django_db
def test_draft_keeps_attachments_and_send_removes_draft(alice, client_for, mailserver, sanitizer):
    from django.core.files.uploadedfile import SimpleUploadedFile

    c = client_for(alice)
    r = c.post(
        "/api/mail/drafts/",
        {"subject": "З вкладенням", "html": "<p>x</p>", "attachments": [SimpleUploadedFile("a.txt", b"hello", "text/plain")]},
        format="multipart",
    ).json()
    att = r["attachments"]
    assert [a["filename"] for a in att] == ["a.txt"]

    # Надсилання з чернетки: вкладення береться з неї, чернетка видаляється
    s = c.post(
        "/api/mail/send/",
        {"to": "bob@example.org", "subject": "З вкладенням", "html": "<p>x</p>", "draft_uid": r["uid"], "keep_attachments": [att[0]["index"]]},
        format="multipart",
    )
    assert s.status_code == 201, s.content
    assert mailserver.store["Drafts"] == {}
    _from, rcpts, raw = FakeSMTP.sent[0]
    assert rcpts == ["bob@example.org"]
    msg = email.message_from_bytes(raw, policy=default_policy)
    assert [p.get_filename() for p in msg.iter_attachments()] == ["a.txt"]
    assert msg["Bcc"] is None
    assert len(mailserver.store["Sent"]) == 1


@pytest.mark.django_db
def test_draft_of_other_mailbox_owner_is_not_accessible(alice, bob, client_for, mailserver):
    from apps.mail.models import Mailbox

    bob_box = Mailbox.objects.get(user=bob)
    r = client_for(alice).get(f"/api/mail/drafts/1/?mailbox={bob_box.pk}")
    assert r.status_code == 404


# ═══════════════════ Відкладене надсилання ═══════════════════


def _when(minutes=60):
    return (timezone.now() + timedelta(minutes=minutes)).isoformat()


@pytest.mark.django_db
def test_schedule_validation(alice, client_for, mailserver, sanitizer):
    c = client_for(alice)
    base = {"to": "bob@example.org", "subject": "S", "html": "<p>x</p>"}
    assert c.post("/api/mail/send/", {**base, "send_at": "завтра"}, format="json").status_code == 400
    assert c.post("/api/mail/send/", {**base, "send_at": _when(-10)}, format="json").status_code == 400
    assert c.post("/api/mail/send/", {**base, "send_at": _when(60 * 24 * 400)}, format="json").status_code == 400
    naive = (timezone.now() + timedelta(hours=1)).replace(tzinfo=None).isoformat()
    assert c.post("/api/mail/send/", {**base, "send_at": naive}, format="json").status_code == 400
    assert FakeSMTP.sent == []


@pytest.mark.django_db
def test_scheduled_message_sent_by_task_and_encrypted_at_rest(alice, client_for, mailserver, sanitizer):
    from apps.mail.tasks import send_scheduled_mail

    c = client_for(alice)
    r = c.post("/api/mail/send/", {"to": "bob@example.org", "subject": "Секретна тема", "html": "<p>пізніше</p>", "send_at": _when()}, format="json")
    assert r.status_code == 201, r.content
    item = ScheduledMessage.objects.get(pk=r.json()["scheduled"]["id"])
    assert "Секретна" not in item.meta_encrypted and "пізніше" not in item.payload_encrypted
    assert FakeSMTP.sent == []

    listed = c.get("/api/mail/scheduled/").json()
    assert listed[0]["subject"] == "Секретна тема" and listed[0]["to"] == "bob@example.org"

    assert send_scheduled_mail() == 0  # ще рано
    ScheduledMessage.objects.filter(pk=item.pk).update(send_at=timezone.now() - timedelta(seconds=1))
    assert send_scheduled_mail() == 1
    item.refresh_from_db()
    assert item.status == "sent" and item.payload_encrypted == ""
    assert len(FakeSMTP.sent) == 1 and FakeSMTP.sent[0][1] == ["bob@example.org"]
    assert send_scheduled_mail() == 0  # повторно не надсилається
    assert c.get("/api/mail/scheduled/").json() == []


@pytest.mark.django_db
def test_scheduled_retries_then_fails_and_notifies(alice, client_for, mailserver, sanitizer):
    from apps.core.models import Notification
    from apps.mail.tasks import MAX_ATTEMPTS, send_scheduled_mail

    r = client_for(alice).post("/api/mail/send/", {"to": "bob@example.org", "subject": "T", "html": "<p>x</p>", "send_at": _when()}, format="json")
    pk = r.json()["scheduled"]["id"]
    FakeSMTP.fail = True
    for _ in range(MAX_ATTEMPTS):
        ScheduledMessage.objects.filter(pk=pk).update(send_at=timezone.now() - timedelta(seconds=1))
        send_scheduled_mail()
    item = ScheduledMessage.objects.get(pk=pk)
    assert item.status == "failed" and item.attempts == MAX_ATTEMPTS
    assert Notification.objects.filter(user=alice, kind="mail").exists()


@pytest.mark.django_db
def test_cancel_scheduled_only_owner_and_wipes_confidential(alice, bob, client_for, mailserver, sanitizer):
    c = client_for(alice)
    r = c.post(
        "/api/mail/send/",
        {"to": "bob@example.org", "subject": "C", "html": "<p>x</p>", "confidential": "1", "confidential_days": "1", "send_at": _when(120)},
        format="json",
    ).json()
    conf = ConfidentialMessage.objects.get(pk=r["confidential"]["id"])
    # термін конфіденційного доступу рахується від часу надсилання (+2 год)
    assert conf.expires_at > timezone.now() + timedelta(days=1, minutes=110)
    pk = r["scheduled"]["id"]
    assert client_for(bob).post(f"/api/mail/scheduled/{pk}/cancel/").status_code == 404
    assert c.post(f"/api/mail/scheduled/{pk}/cancel/").status_code == 200
    item = ScheduledMessage.objects.get(pk=pk)
    assert item.status == "cancelled" and item.payload_encrypted == ""
    assert not ConfidentialMessage.objects.filter(pk=conf.pk).exists()


@pytest.mark.django_db
def test_scheduled_date_header_is_actual_send_time(alice, client_for, mailserver, sanitizer):
    from apps.mail.tasks import send_scheduled_mail

    r = client_for(alice).post("/api/mail/send/", {"to": "bob@example.org", "subject": "D", "body": "t", "send_at": _when(600)}, format="json")
    ScheduledMessage.objects.filter(pk=r.json()["scheduled"]["id"]).update(send_at=timezone.now() - timedelta(seconds=1))
    send_scheduled_mail()
    msg = email.message_from_bytes(FakeSMTP.sent[0][2], policy=default_policy)
    from email.utils import parsedate_to_datetime

    assert abs((parsedate_to_datetime(msg["Date"]) - timezone.now()).total_seconds()) < 60
    assert re.search(r"^Bcc:", FakeSMTP.sent[0][2].decode(errors="ignore"), re.M) is None
