"""Тести: групові дії, віртуальна тека «З зірочкою», заблоковані відправники, список конфіденційних листів."""

import pytest

from apps.mail import imap
from apps.mail.models import BlockedSender, ConfidentialMessage, Mailbox

SPECIAL = {"INBOX": "", "Sent": "\\Sent", "Drafts": "\\Drafts", "Trash": "\\Trash", "Junk": "\\Junk", "Archive": "\\Archive"}


def _raw(sender, subject, date="Mon, 05 Oct 2026 10:00:00 +0000"):
    return f"From: {sender}\r\nTo: alice@example.com\r\nSubject: {subject}\r\nDate: {date}\r\n\r\nbody\r\n".encode()


class FakeIMAP:
    """IMAP у пам'яті з наборами UID: SEARCH ALL/FLAGGED/FROM/SUBJECT (літерал), FETCH, STORE, MOVE, EXPUNGE."""

    store: dict = {}
    commands: list = []

    def __init__(self):
        self.selected = None
        self.literal = None

    @classmethod
    def reset(cls, with_archive=True):
        cls.store = {name: {} for name in SPECIAL if with_archive or name != "Archive"}
        cls.store[imap.encode_folder("Проєкти")] = {}
        cls.next_uid = 1
        cls.commands = []

    @classmethod
    def add(cls, folder, sender, subject, flags=(), day=1):
        uid = cls.next_uid
        cls.next_uid += 1
        cls.store[imap.encode_folder(folder)][uid] = {
            "raw": _raw(sender, subject),
            "flags": set(flags),
            "date": f"{day:02d}-Oct-2026 10:00:00 +0000",
        }
        return uid

    @classmethod
    def folder_of(cls, subject):
        for name, box in cls.store.items():
            for uid, item in box.items():
                if f"Subject: {subject}\r\n".encode() in item["raw"]:
                    return imap.decode_folder(name), uid, item
        return None, None, None

    def login(self, *a):
        return "OK", [b""]

    def logout(self):
        return "BYE", [b""]

    def list(self, *a):
        out = []
        for name in self.store:
            special = SPECIAL.get(name, "")
            out.append(f'(\\HasNoChildren{" " + special if special else ""}) "/" "{name}"'.encode())
        return "OK", out

    def select(self, name, readonly=False):
        name = name.strip('"')
        if name not in self.store:
            return "NO", [b""]
        self.selected = name
        return "OK", [str(len(self.store[name])).encode()]

    @staticmethod
    def _uids(spec):
        return [int(x) for x in str(spec).split(",")]

    def uid(self, cmd, *args):
        FakeIMAP.commands.append((cmd, self.selected) + tuple(str(a) for a in args))
        box = self.store[self.selected]
        if cmd == "SEARCH":
            if args[-1] == "ALL":
                hits = list(box)
            elif args[-1] == "FLAGGED":
                hits = [u for u, v in box.items() if "\\Flagged" in v["flags"]]
            elif args[-1] in ("FROM", "SUBJECT"):
                needle = self.literal.decode().lower()
                self.literal = None
                field = args[-1].capitalize().encode()
                hits = []
                for u, v in box.items():
                    for line in v["raw"].split(b"\r\n"):
                        if line.startswith(field + b": ") and needle in line.decode().lower():
                            hits.append(u)
            else:
                raise AssertionError(args)
            return "OK", [" ".join(map(str, sorted(hits))).encode()]
        if cmd == "FETCH":
            what = args[1]
            out = []
            for u in self._uids(args[0]):
                item = box.get(u)
                if not item:
                    continue
                flags = " ".join(sorted(item["flags"]))
                if what == "(UID INTERNALDATE)":
                    out.append(f'{u} (UID {u} INTERNALDATE "{item["date"]}")'.encode())
                    continue
                payload = item["raw"]
                if "HEADER.FIELDS" in what:
                    payload = payload.split(b"\r\n\r\n")[0] + b"\r\n\r\n"
                meta = f"{u} (UID {u} FLAGS ({flags}) RFC822.SIZE {len(item['raw'])} BODY[] {{{len(payload)}}}".encode()
                out += [(meta, payload), b")"]
            return "OK", out or [None]
        if cmd == "STORE":
            op, flag = args[1], args[2].strip("()")
            for u in self._uids(args[0]):
                if u in box:
                    (box[u]["flags"].add if op == "+FLAGS" else box[u]["flags"].discard)(flag)
            return "OK", [b""]
        if cmd == "MOVE":
            target = args[1].strip('"')
            if target not in self.store:
                return "NO", [b"no such mailbox"]
            for u in self._uids(args[0]):
                if u in box:
                    new = FakeIMAP.next_uid
                    FakeIMAP.next_uid += 1
                    self.store[target][new] = box.pop(u)
            return "OK", [b""]
        if cmd == "EXPUNGE":
            for u in self._uids(args[0]):
                if u in box and "\\Deleted" in box[u]["flags"]:
                    del box[u]
            return "OK", [b""]
        raise AssertionError(cmd)


@pytest.fixture
def mailserver(monkeypatch, settings):
    from contextlib import contextmanager

    settings.DOVECOT_MASTER_PASSWORD = "test"
    FakeIMAP.reset()

    @contextmanager
    def session(address):
        yield FakeIMAP()

    monkeypatch.setattr(imap, "imap_session", session)
    return FakeIMAP


def _bulk(c, **data):
    return c.post("/api/mail/messages/bulk/", data, format="json")


# ═══════════════════ Групові дії ═══════════════════


@pytest.mark.django_db
def test_bulk_flags_use_one_uid_set(alice, client_for, mailserver):
    a = mailserver.add("INBOX", "x@example.org", "a")
    b = mailserver.add("INBOX", "x@example.org", "b")
    c = client_for(alice)
    r = _bulk(c, folder="INBOX", uids=[b, a, a], action="seen")
    assert r.status_code == 200, r.content
    assert r.json()["count"] == 2
    stores = [cmd for cmd in mailserver.commands if cmd[0] == "STORE"]
    assert stores == [("STORE", "INBOX", f"{a},{b}", "+FLAGS", "(\\Seen)")]
    assert all("\\Seen" in mailserver.store["INBOX"][u]["flags"] for u in (a, b))
    assert _bulk(c, folder="INBOX", uids=[a], action="flag").status_code == 200
    assert "\\Flagged" in mailserver.store["INBOX"][a]["flags"]
    assert _bulk(c, folder="INBOX", uids=[a, b], action="unseen").status_code == 200
    assert _bulk(c, folder="INBOX", uids=[a], action="unflag").status_code == 200
    assert mailserver.store["INBOX"][a]["flags"] == set()
    from apps.core.models import AuditLog

    assert AuditLog.objects.filter(action="mail.bulk", user=alice).count() == 4


@pytest.mark.django_db
def test_bulk_delete_moves_to_trash_then_expunges(alice, client_for, mailserver):
    uids = [mailserver.add("INBOX", "x@example.org", f"m{i}") for i in range(3)]
    c = client_for(alice)
    r = _bulk(c, folder="INBOX", uids=uids[:2], action="delete")
    assert r.status_code == 200 and r.json()["target"] == "Trash"
    assert list(mailserver.store["INBOX"]) == [uids[2]]
    trash_uids = list(mailserver.store["Trash"])
    assert len(trash_uids) == 2
    r = _bulk(c, folder="Trash", uids=trash_uids, action="delete")
    assert r.status_code == 200 and r.json()["permanent"] is True
    assert mailserver.store["Trash"] == {}


@pytest.mark.django_db
def test_bulk_spam_notspam_archive_move(alice, client_for, mailserver):
    u1 = mailserver.add("INBOX", "x@example.org", "one")
    u2 = mailserver.add("INBOX", "x@example.org", "two")
    c = client_for(alice)
    assert _bulk(c, folder="INBOX", uids=[u1], action="spam").json()["target"] == "Junk"
    assert mailserver.folder_of("one")[0] == "Junk"
    junk_uid = mailserver.folder_of("one")[1]
    assert _bulk(c, folder="Junk", uids=[junk_uid], action="notspam").json()["target"] == "INBOX"
    assert mailserver.folder_of("one")[0] == "INBOX"
    assert _bulk(c, folder="INBOX", uids=[u2], action="archive").status_code == 200
    assert mailserver.folder_of("two")[0] == "Archive"
    arch = mailserver.folder_of("two")[1]
    r = _bulk(c, folder="Archive", uids=[arch], action="move", target="Проєкти")
    assert r.status_code == 200, r.content
    assert mailserver.folder_of("two")[0] == "Проєкти"
    # Ціль — та сама тека / немає цілі / тека не існує
    proj = mailserver.folder_of("two")[1]
    assert _bulk(c, folder="Проєкти", uids=[proj], action="move", target="Проєкти").status_code == 400
    assert _bulk(c, folder="Проєкти", uids=[proj], action="move").status_code == 400
    assert _bulk(c, folder="Проєкти", uids=[proj], action="move", target="Nope").status_code == 400


@pytest.mark.django_db
def test_bulk_archive_without_archive_folder(alice, client_for, mailserver):
    mailserver.reset(with_archive=False)
    uid = mailserver.add("INBOX", "x@example.org", "one")
    r = _bulk(client_for(alice), folder="INBOX", uids=[uid], action="archive")
    assert r.status_code == 400
    assert mailserver.folder_of("one")[0] == "INBOX"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "data",
    [
        {"folder": "INBOX", "uids": [], "action": "seen"},
        {"folder": "INBOX", "uids": "1,2", "action": "seen"},
        {"folder": "INBOX", "uids": ["1 OR 2"], "action": "seen"},
        {"folder": "INBOX", "uids": ["1:*"], "action": "seen"},
        {"folder": "INBOX", "uids": [0], "action": "seen"},
        {"folder": "INBOX", "uids": [True], "action": "seen"},
        {"folder": "INBOX", "uids": list(range(1, 502)), "action": "seen"},
        {"folder": "INBOX", "uids": [1], "action": "explode"},
        {"folder": "__starred__", "uids": [1], "action": "seen"},
        {"folder": "x" * 201, "uids": [1], "action": "seen"},
        {"folder": ["INBOX"], "uids": [1], "action": "seen"},
    ],
)
def test_bulk_validation(alice, client_for, mailserver, data):
    r = _bulk(client_for(alice), **data)
    assert r.status_code == 400, (data, r.content)
    assert not [c for c in mailserver.commands if c[0] in ("STORE", "MOVE", "EXPUNGE")]


@pytest.mark.django_db
def test_bulk_max_500_ok_and_foreign_mailbox(alice, bob, client_for, mailserver):
    c = client_for(alice)
    assert _bulk(c, folder="INBOX", uids=list(range(1, 501)), action="seen").json()["count"] == 500
    bob_box = Mailbox.objects.get(user=bob)
    r = _bulk(c, mailbox=str(bob_box.pk), folder="INBOX", uids=[1], action="delete")
    assert r.status_code == 404


@pytest.mark.django_db
def test_single_message_endpoints_still_work_and_return_flagged(alice, client_for, mailserver):
    uid = mailserver.add("INBOX", "x@example.org", "solo", flags=["\\Flagged"])
    c = client_for(alice)
    r = c.get(f"/api/mail/messages/{uid}/?folder=INBOX")
    assert r.status_code == 200 and r.json()["flagged"] is True
    assert "\\Seen" in mailserver.store["INBOX"][uid]["flags"]
    assert c.patch(f"/api/mail/messages/{uid}/", {"folder": "INBOX", "flagged": False}, format="json").status_code == 200
    assert c.get(f"/api/mail/messages/{uid}/?folder=INBOX").json()["flagged"] is False
    assert c.delete(f"/api/mail/messages/{uid}/?folder=INBOX").status_code == 204
    assert mailserver.folder_of("solo")[0] == "Trash"


# ═══════════════════ «З зірочкою» ═══════════════════


@pytest.mark.django_db
def test_starred_across_folders(alice, client_for, mailserver):
    mailserver.add("INBOX", "a@example.org", "inbox-old", flags=["\\Flagged"], day=1)
    mailserver.add("INBOX", "a@example.org", "inbox-plain", day=9)
    mailserver.add("Archive", "b@example.org", "archive-new", flags=["\\Flagged", "\\Seen"], day=8)
    mailserver.add("Проєкти", "c@example.org", "project-mid", flags=["\\Flagged"], day=5)
    mailserver.add("Trash", "d@example.org", "trash-starred", flags=["\\Flagged"], day=9)
    mailserver.add("Junk", "e@example.org", "junk-starred", flags=["\\Flagged"], day=9)
    c = client_for(alice)
    r = c.get("/api/mail/messages/?folder=__starred__")
    assert r.status_code == 200, r.content
    data = r.json()
    assert data["total"] == 3
    assert [(m["subject"], m["folder"]) for m in data["results"]] == [
        ("archive-new", "Archive"),
        ("project-mid", "Проєкти"),
        ("inbox-old", "INBOX"),
    ]
    assert all(m["flagged"] for m in data["results"])
    # Звичайний список теж повертає folder
    assert c.get("/api/mail/messages/?folder=INBOX").json()["results"][0]["folder"] == "INBOX"
    # Пошук у «З зірочкою»
    r = c.get("/api/mail/messages/?folder=__starred__&q=project")
    assert [m["subject"] for m in r.json()["results"]] == ["project-mid"]


@pytest.mark.django_db
def test_starred_pagination_and_scan_limit(alice, mailserver, monkeypatch):
    for day in range(1, 8):
        mailserver.add("INBOX", "a@example.org", f"s{day}", flags=["\\Flagged"], day=day)
    page = imap.list_starred("alice@example.com", page=2, page_size=3)
    assert page["total"] == 7 and [m["subject"] for m in page["results"]] == ["s4", "s3", "s2"]
    monkeypatch.setattr(imap, "STARRED_SCAN_LIMIT", 5)
    capped = imap.list_starred("alice@example.com", page=1, page_size=10)
    assert capped["total"] == 5 and capped["truncated"] is True
    assert capped["results"][0]["subject"] == "s7"


# ═══════════════════ Заблоковані відправники ═══════════════════


@pytest.mark.parametrize(
    "value, expected",
    [
        ("Spam@Evil.COM", "spam@evil.com"),
        ('"Спамер" <Spam@evil.com>', "spam@evil.com"),
        ("@Evil.Org", "@evil.org"),
        ("іван@example.com", "іван@example.com"),
    ],
)
def test_normalize_block_entry_ok(value, expected):
    assert imap.normalize_block_entry(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "nobody", "@", "@localhost", "a@b", "a b@evil.com", "a@evil.com\r\nX: y", "@evil..com", "a@-evil.com", "x" * 400, None, "a\"b@evil.com"],
)
def test_normalize_block_entry_rejects(value):
    from rest_framework.exceptions import ValidationError

    with pytest.raises(ValidationError):
        imap.normalize_block_entry(value)


def test_sender_matches_is_exact():
    entries = ["spam@evil.com", "@bad.org"]
    assert imap.sender_matches("Spam <SPAM@evil.com>", entries)
    assert imap.sender_matches("x@bad.org", entries)
    assert not imap.sender_matches("notspam@evil.com", entries)
    assert not imap.sender_matches("spam@evil.com.ua", entries)
    assert not imap.sender_matches("x@sub.bad.org", entries)
    assert not imap.sender_matches("", entries)


@pytest.mark.django_db
def test_block_sender_moves_existing_inbox_mail(alice, client_for, mailserver):
    mailserver.add("INBOX", "Спамер <spam@evil.com>", "spam1")
    mailserver.add("INBOX", "SPAM@EVIL.COM", "spam2")
    mailserver.add("INBOX", "notspam@evil.com", "lookalike")
    mailserver.add("INBOX", "spam@evil.com.ua", "lookalike2")
    mailserver.add("Archive", "spam@evil.com", "archived")
    c = client_for(alice)
    r = c.post("/api/mail/blocked/", {"address": "Спамер <Spam@Evil.com>"}, format="json")
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["moved"] == 2
    assert [b["address"] for b in body["blocked"]] == ["spam@evil.com"]
    assert mailserver.folder_of("spam1")[0] == "Junk" and mailserver.folder_of("spam2")[0] == "Junk"
    assert mailserver.folder_of("lookalike")[0] == "INBOX"
    assert mailserver.folder_of("lookalike2")[0] == "INBOX"
    assert mailserver.folder_of("archived")[0] == "Archive"  # лише «Вхідні»
    # SEARCH FROM — літералом (без лапок і CRLF-ін'єкції)
    assert ("SEARCH", "INBOX", "CHARSET", "UTF-8", "FROM") in mailserver.commands
    # повторне блокування не дублює
    assert c.post("/api/mail/blocked/", {"address": "spam@evil.com"}, format="json").status_code == 201
    assert BlockedSender.objects.filter(mailbox__user=alice).count() == 1
    listed = c.get("/api/mail/blocked/").json()
    assert [(b["address"], b["mailbox_address"]) for b in listed] == [("spam@evil.com", "alice@example.com")]


@pytest.mark.django_db
def test_block_domain_and_many(alice, client_for, mailserver):
    mailserver.add("INBOX", "a@bad.org", "d1")
    mailserver.add("INBOX", "b@sub.bad.org", "d2")
    mailserver.add("INBOX", "c@x.com", "x1")
    r = client_for(alice).post("/api/mail/blocked/", {"addresses": ["@bad.org", "c@x.com", "c@x.com"]}, format="json")
    assert r.status_code == 201 and r.json()["moved"] == 2
    assert mailserver.folder_of("d1")[0] == "Junk" and mailserver.folder_of("x1")[0] == "Junk"
    assert mailserver.folder_of("d2")[0] == "INBOX"


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["alice@example.com", "@example.com", "bad", ""])
def test_block_rejects_own_and_invalid(alice, client_for, mailserver, value):
    r = client_for(alice).post("/api/mail/blocked/", {"address": value}, format="json")
    assert r.status_code == 400
    assert not BlockedSender.objects.exists()


@pytest.mark.django_db
def test_block_limits(alice, client_for, mailserver, monkeypatch):
    from apps.mail import views

    c = client_for(alice)
    assert c.post("/api/mail/blocked/", {"addresses": [f"a{i}@x.com" for i in range(101)]}, format="json").status_code == 400
    monkeypatch.setattr(views, "MAX_BLOCKED", 2)
    assert c.post("/api/mail/blocked/", {"addresses": ["a@x.com", "b@x.com"]}, format="json").status_code == 201
    assert c.post("/api/mail/blocked/", {"address": "c@x.com"}, format="json").status_code == 400


@pytest.mark.django_db
def test_block_saved_even_if_imap_down(alice, client_for, settings, monkeypatch):
    settings.DOVECOT_MASTER_PASSWORD = ""  # imap_session кидає MailUnavailable
    r = client_for(alice).post("/api/mail/blocked/", {"address": "spam@evil.com"}, format="json")
    assert r.status_code == 201 and r.json()["moved"] == 0
    assert BlockedSender.objects.filter(address="spam@evil.com").exists()


@pytest.mark.django_db
def test_unblock_and_isolation(alice, bob, client_for, mailserver):
    alice_box = Mailbox.objects.get(user=alice)
    bob_box = Mailbox.objects.get(user=bob)
    item = BlockedSender.objects.create(mailbox=alice_box, address="spam@evil.com")
    BlockedSender.objects.create(mailbox=bob_box, address="other@evil.com")
    cb = client_for(bob)
    assert [b["address"] for b in cb.get("/api/mail/blocked/").json()] == ["other@evil.com"]
    assert cb.delete(f"/api/mail/blocked/{item.pk}/").status_code == 404
    assert cb.post("/api/mail/blocked/", {"mailbox": str(alice_box.pk), "address": "x@evil.com"}, format="json").status_code == 404
    assert client_for(alice).delete(f"/api/mail/blocked/{item.pk}/").status_code == 204
    assert not BlockedSender.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db
def test_enforce_blocklist_task(alice, bob, mailserver):
    from apps.mail.tasks import enforce_blocklist

    BlockedSender.objects.create(mailbox=Mailbox.objects.get(user=alice), address="spam@evil.com")
    mailserver.add("INBOX", "spam@evil.com", "new-spam")
    mailserver.add("INBOX", "friend@ok.com", "friend")
    assert enforce_blocklist() == 1
    assert mailserver.folder_of("new-spam")[0] == "Junk"
    assert mailserver.folder_of("friend")[0] == "INBOX"
    assert enforce_blocklist() == 0


@pytest.mark.django_db
def test_mailwatch_skips_blocked_senders(alice, monkeypatch):
    from contextlib import contextmanager

    from apps.push import mailwatch

    from .test_push import FakeInbox

    FakeInbox.reset()

    @contextmanager
    def session(address):
        yield FakeInbox()

    monkeypatch.setattr(imap, "imap_session", session)
    mb = Mailbox.objects.get(user=alice)
    BlockedSender.objects.create(mailbox=mb, address="@evil.com")
    sent = []
    monkeypatch.setattr("apps.push.service.send_to_user", lambda *a, **kw: sent.append(a[2]))
    assert mailwatch.check_mailbox(mb) == 0  # перший прохід запам'ятовує стан
    FakeInbox.deliver("Spammer <x@evil.com>", "buy")
    FakeInbox.deliver("Friend <f@ok.com>", "hi")
    mailwatch.check_mailbox(mb)
    assert sent == ["Friend"]


# ═══════════════════ Конфіденційні листи ═══════════════════


@pytest.mark.django_db
def test_confidential_list_returns_recipient_list(alice, client_for):
    from datetime import timedelta

    from django.utils import timezone

    ConfidentialMessage.objects.create(
        sender=alice,
        from_address="alice@example.com",
        recipients="a@x.com, b@y.com",
        subject="s",
        html_encrypted="x",
        token_hash="h" * 64,
        expires_at=timezone.now() + timedelta(days=1),
    )
    ConfidentialMessage.objects.create(
        sender=alice, from_address="alice@example.com", recipients="", subject="t", token_hash="g" * 64,
        expires_at=timezone.now() + timedelta(days=1),
    )
    data = client_for(alice).get("/api/mail/confidential/").json()
    assert sorted(tuple(m["recipients"]) for m in data) == [(), ("a@x.com", "b@y.com")]

