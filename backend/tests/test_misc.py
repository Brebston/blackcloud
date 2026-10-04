from datetime import datetime, timedelta, timezone

import pytest

from apps.calendars.recurrence import occurrences, validate_rrule
from apps.mail.imap import decode_folder, encode_folder, quote
from apps.mail.passwords import dovecot_hash, generate_client_password
from apps.mail.sanitize import BLOCKED_IMG, sanitize_html


def test_utf7_roundtrip():
    for name in ["INBOX", "Надіслані", "A&B", "Звіти/2026"]:
        assert decode_folder(encode_folder(name)) == name
    assert encode_folder("A&B") == "A&-B"


def test_quote_escapes():
    assert quote('a"b') == '"a\\"b"'


def test_sanitize_removes_scripts_and_trackers():
    html = (
        '<p onclick="x()">Hi<script>alert(1)</script></p>'
        '<img src="https://tracker.example/p.gif">'
        '<a href="javascript:alert(1)">x</a>'
        '<div style="background:url(https://t.example/a)">y</div>'
        '<img src="cid:logo">'
    )
    out = sanitize_html(html, inline_images={"logo": "data:image/png;base64,AAAA"}, allow_remote=False)
    assert "script" not in out and "onclick" not in out
    assert "tracker.example" not in out and BLOCKED_IMG in out
    assert "javascript:" not in out
    assert "t.example" not in out
    assert "data:image/png;base64,AAAA" in out


def test_sanitize_allows_remote_when_enabled():
    out = sanitize_html('<img src="https://cdn.example/a.png">', inline_images={}, allow_remote=True)
    assert "https://cdn.example/a.png" in out


def test_dovecot_hash_format():
    pw = generate_client_password()
    assert len(pw) == 29
    h = dovecot_hash(pw)
    assert h.startswith("{ARGON2ID}$argon2id$") and ",p=1$" in h


def test_rrule_validation_and_expansion():
    start = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
    rule = validate_rrule("FREQ=WEEKLY;BYDAY=MO;COUNT=10", start)

    class Ev:
        pass

    ev = Ev()
    ev.start, ev.end, ev.rrule = start, start + timedelta(hours=1), rule
    occ = occurrences(ev, datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert len(occ) == 4


def test_rrule_rejects_dangerous():
    from rest_framework.exceptions import ValidationError

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        validate_rrule("FREQ=SECONDLY", start)
    with pytest.raises(ValidationError):
        validate_rrule("FREQ=DAILY;X-EVIL=1", start)


@pytest.mark.django_db
def test_mailbox_and_calendar_autocreated(alice):
    assert alice.mailbox.address == f"alice@{__import__('django.conf').conf.settings.MAIL_DOMAIN}"
    assert alice.calendars.count() == 1


@pytest.mark.django_db
def test_chat_direct_conversation_unique_and_private(alice, bob, make_user, client_for):
    ca, cb = client_for(alice), client_for(bob)
    c1 = ca.post("/api/chat/conversations/", {"usernames": ["bob"]}, format="json").json()
    c2 = cb.post("/api/chat/conversations/", {"usernames": ["alice"]}, format="json").json()
    assert c1["id"] == c2["id"]
    assert ca.post(f"/api/chat/conversations/{c1['id']}/messages/", {"body": "привіт"}, format="json").status_code == 201
    eve = make_user("eve")
    ce = client_for(eve)
    assert ce.get(f"/api/chat/conversations/{c1['id']}/messages/").status_code == 404
    assert ce.post(f"/api/chat/conversations/{c1['id']}/messages/", {"body": "x"}, format="json").status_code == 404


@pytest.mark.django_db
def test_calendar_events_range(alice, client_for):
    c = client_for(alice)
    cal = c.get("/api/calendars/").json()[0]
    r = c.post(
        "/api/calendars/events/",
        {
            "calendar": cal["id"],
            "title": "Стендап",
            "start": "2026-03-02T09:00:00Z",
            "end": "2026-03-02T09:15:00Z",
            "rrule": "FREQ=DAILY;COUNT=5",
        },
        format="json",
    )
    assert r.status_code == 201, r.content
    events = c.get("/api/calendars/events/?start=2026-03-01T00:00:00Z&end=2026-03-31T00:00:00Z").json()
    assert len(events) == 5


@pytest.mark.django_db
def test_cannot_add_event_to_foreign_calendar(alice, bob, client_for):
    cal_b = client_for(bob).get("/api/calendars/").json()[0]
    r = client_for(alice).post(
        "/api/calendars/events/",
        {"calendar": cal_b["id"], "title": "x", "start": "2026-03-02T09:00:00Z", "end": "2026-03-02T10:00:00Z"},
        format="json",
    )
    assert r.status_code == 403
