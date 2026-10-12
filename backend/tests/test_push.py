"""Тести: Web Push (RFC 8291/8292), API підписок, тригери (сповіщення, чат, пошта), надсилання."""

import json
import urllib.error
from unittest import mock

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from django.utils.translation import gettext_noop

from apps.push import webpush
from apps.push.models import PushSubscription

d = webpush.b64u_decode

# ─────────────────────── RFC 8291, Appendix A ───────────────────────

RFC = {
    "plaintext": "V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24",
    "as_private": "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
    "as_public": "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8",
    "ua_public": "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
    "salt": "DGv6ra1nlYgDCS1FRnbzlw",
    "auth": "BTBZMqHH6r4Tts7J_aSIgg",
    "shared": "kyrL1jIIOHEzg3sM2ZWRHDRB62YACZhhSlknJ672kSs",
    "prk_key": "Snr3JMxaHVDXHWJn5wdC52WjpCtd2EIEGBykDcZW32k",
    "ikm": "S4lYMb_L0FxCeq0WhDx813KgSYqU26kOyzWUdsXYyrg",
    "prk": "09_eUZGrsvxChDCGRCdkLiDXrReGOEVeSCdCcPBSJSc",
    "cek": "oIhVW04MRdy2XN9CiKLxTg",
    "nonce": "4h_95klXJ5E_qnoN",
    "message": "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN",
}


def _rfc_as_private():
    return ec.derive_private_key(int.from_bytes(d(RFC["as_private"]), "big"), ec.SECP256R1())


def test_rfc8291_intermediate_values():
    k = webpush.derive(d(RFC["ua_public"]), d(RFC["auth"]), d(RFC["salt"]), _rfc_as_private())
    for name in ("as_public", "shared", "prk_key", "ikm", "prk", "cek", "nonce"):
        assert webpush.b64u_encode(k[name]) == RFC[name], name


def test_rfc8291_full_message():
    out = webpush.encrypt(
        d(RFC["plaintext"]), d(RFC["ua_public"]), d(RFC["auth"]), salt=d(RFC["salt"]), as_private=_rfc_as_private()
    )
    assert webpush.b64u_encode(out) == RFC["message"]


def test_encrypt_random_keys_and_limits():
    ua = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    a, b = webpush.encrypt(b"hello", ua, b"x" * 16), webpush.encrypt(b"hello", ua, b"x" * 16)
    assert len(a) == 86 + 5 + 1 + 16 and a != b  # новий salt і ефемерний ключ щоразу
    with pytest.raises(ValueError):
        webpush.encrypt(b"x" * 4000, ua, b"x" * 16)
    with pytest.raises(ValueError):
        webpush.load_public_point(webpush.b64u_encode(b"\x04" + b"\x01" * 64))  # не на кривій


# ─────────────────────── VAPID ───────────────────────


def _pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def test_vapid_jwt_structure_and_signature():
    key = ec.generate_private_key(ec.SECP256R1())
    token = webpush.vapid_jwt("https://fcm.googleapis.com", "mailto:admin@example.org", key, now=1_000_000)
    h, c, s = token.split(".")
    assert json.loads(d(h)) == {"typ": "JWT", "alg": "ES256"}
    assert json.loads(d(c)) == {"aud": "https://fcm.googleapis.com", "exp": 1_000_000 + 12 * 3600, "sub": "mailto:admin@example.org"}
    sig = d(s)
    assert len(sig) == 64
    der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
    key.public_key().verify(der, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))  # не кидає виняток


def test_public_key_formats():
    key = ec.generate_private_key(ec.SECP256R1())
    b64 = webpush.public_key_b64(key)
    assert len(d(b64)) == 65 and d(b64)[0] == 4
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    assert webpush.normalize_public_key(pem) == b64
    assert webpush.normalize_public_key(b64) == b64
    assert webpush.normalize_public_key("") == ""
    assert webpush.public_key_b64(webpush.private_key_from_pem(_pem(key))) == b64


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://fcm.googleapis.com/fcm/send/abc", True),
        ("https://updates.push.services.mozilla.com/wpush/v2/abc", True),
        ("https://web.push.apple.com/QGx", True),
        ("https://api.push.apple.com/3/device/x", True),
        ("https://wns2-par02p.notify.windows.com/w/?token=x", True),
        ("http://fcm.googleapis.com/fcm/send/abc", False),
        ("https://fcm.googleapis.com:8443/x", False),
        ("https://user:pw@fcm.googleapis.com/x", False),
        ("https://fcm.googleapis.com.evil.com/x", False),
        ("https://evilpush.services.mozilla.com/x", False),
        ("https://push.services.mozilla.com/x", False),
        ("https://storage.googleapis.com/bucket", False),
        ("https://127.0.0.1/x", False),
        ("https://backend:8000/api/", False),
        ("https://fcm.googleapis.com/x\r\nHost: a", False),
        ("ftp://fcm.googleapis.com/x", False),
    ],
)
def test_endpoint_allowlist(url, ok):
    assert webpush.endpoint_allowed(url) is ok


# ─────────────────────── API підписок ───────────────────────


@pytest.fixture
def vapid(settings):
    key = ec.generate_private_key(ec.SECP256R1())
    settings.VAPID_PRIVATE_KEY = _pem(key)
    settings.VAPID_PUBLIC_KEY = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    settings.VAPID_SUBJECT = "mailto:admin@example.org"
    return key


def _browser_keys():
    ua = ec.generate_private_key(ec.SECP256R1())
    p256dh = webpush.b64u_encode(
        ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    return p256dh, webpush.b64u_encode(b"0123456789abcdef")


def _sub_data(n=0, **extra):
    p256dh, auth = _browser_keys()
    return {"endpoint": f"https://fcm.googleapis.com/fcm/send/dev{n}", "p256dh": p256dh, "auth": auth, **extra}


def _subscribe(user, n=0, **extra):
    p256dh, auth = _browser_keys()
    return PushSubscription.objects.create(
        user=user, endpoint=f"https://fcm.googleapis.com/fcm/send/{user.username}{n}", p256dh=p256dh, auth=auth, **extra
    )


@pytest.mark.django_db
def test_vapid_key_endpoint(alice, client_for, vapid, settings):
    r = client_for(alice).get("/api/push/vapid-key/")
    assert r.status_code == 200 and r.json() == {"public_key": webpush.public_key_b64(vapid), "enabled": True}
    from rest_framework.test import APIClient

    assert APIClient().get("/api/push/vapid-key/").status_code in (401, 403)
    settings.VAPID_PUBLIC_KEY = ""
    assert client_for(alice).get("/api/push/vapid-key/").json()["enabled"] is False
    assert client_for(alice).post("/api/push/subscriptions/", _sub_data(), format="json").status_code == 400


@pytest.mark.django_db
def test_subscribe_upsert_and_delete(alice, bob, client_for, vapid):
    c = client_for(alice)
    data = _sub_data()
    r = c.post("/api/push/subscriptions/", data, format="json", HTTP_USER_AGENT="Firefox/130")
    assert r.status_code == 201 and r.json()["notify_chat"] is True and r.json()["user_agent"] == "Firefox/130"
    r = c.post("/api/push/subscriptions/", {**data, "notify_chat": False, "show_previews": False}, format="json")
    assert r.status_code == 200 and r.json()["notify_chat"] is False
    listed = c.get("/api/push/subscriptions/").json()
    assert listed["count"] == 1 and listed["results"][0]["show_previews"] is False
    # Чужу підписку видалити не можна
    assert client_for(bob).delete("/api/push/subscriptions/", {"endpoint": data["endpoint"]}, format="json").status_code == 204
    assert PushSubscription.objects.filter(user=alice).count() == 1
    assert c.delete("/api/push/subscriptions/", {}, format="json").status_code == 400
    assert c.delete("/api/push/subscriptions/", {"endpoint": data["endpoint"]}, format="json").status_code == 204
    assert not PushSubscription.objects.exists()


@pytest.mark.django_db
def test_subscription_change_keeps_preferences(alice, bob, client_for, vapid):
    old = _subscribe(alice, notify_mail=False, show_previews=False)
    foreign = _subscribe(bob, n=5, notify_chat=False)
    c = client_for(alice)
    r = c.post("/api/push/subscriptions/", {**_sub_data(1), "replaces": old.endpoint}, format="json")
    assert r.status_code == 201 and r.json()["notify_mail"] is False and r.json()["show_previews"] is False
    assert not PushSubscription.objects.filter(pk=old.pk).exists()
    # Чужу підписку так «перехопити» не можна
    r = c.post("/api/push/subscriptions/", {**_sub_data(2), "replaces": foreign.endpoint}, format="json")
    assert r.json()["notify_chat"] is True and PushSubscription.objects.filter(pk=foreign.pk).exists()


@pytest.mark.django_db
def test_same_browser_moves_to_new_account(alice, bob, client_for, vapid):
    data = _sub_data()
    client_for(alice).post("/api/push/subscriptions/", data, format="json")
    assert client_for(bob).post("/api/push/subscriptions/", data, format="json").status_code == 201
    assert list(PushSubscription.objects.values_list("user__username", flat=True)) == ["bob"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "patch",
    [
        {"endpoint": "https://evil.example.com/push"},
        {"endpoint": "http://fcm.googleapis.com/fcm/send/x"},
        {"endpoint": "https://172.30.0.10/api/"},
        {"p256dh": "AAAA"},
        {"p256dh": webpush.b64u_encode(b"\x04" + b"\x01" * 64)},
        {"auth": webpush.b64u_encode(b"short")},
    ],
)
def test_subscribe_rejects_bad_input(alice, client_for, vapid, patch):
    r = client_for(alice).post("/api/push/subscriptions/", {**_sub_data(), **patch}, format="json")
    assert r.status_code == 400
    assert not PushSubscription.objects.exists()


@pytest.mark.django_db
def test_per_user_limit_drops_oldest(alice, client_for, vapid):
    c = client_for(alice)
    for n in range(11):
        assert c.post("/api/push/subscriptions/", _sub_data(n), format="json").status_code == 201
    endpoints = set(PushSubscription.objects.filter(user=alice).values_list("endpoint", flat=True))
    assert len(endpoints) == 10 and "https://fcm.googleapis.com/fcm/send/dev0" not in endpoints


@pytest.mark.django_db
def test_test_notification_goes_to_all_devices(alice, client_for, vapid):
    c = client_for(alice)
    with mock.patch("apps.push.tasks.send_push.delay") as delay:
        assert c.post("/api/push/test/").status_code == 400  # немає пристроїв
        _subscribe(alice, notify_other=False, notify_mail=False, notify_chat=False)
        r = c.post("/api/push/test/")
    assert r.status_code == 200 and r.json() == {"sent": 1}
    payload = json.loads(delay.call_args.args[4])
    assert payload["kind"] == "test" and payload["url"] == "/settings"


# ─────────────────────── Тригери ───────────────────────


@pytest.mark.django_db
def test_notify_enqueues_push(alice, vapid):
    from apps.core.realtime import notify

    sub = _subscribe(alice)
    with mock.patch("apps.push.tasks.send_push.delay") as delay:
        notify(alice, "security", gettext_noop("Новий вхід"), "", "/settings")
    assert delay.call_count == 1
    args = delay.call_args.args
    assert args[0] == str(sub.pk) and args[1] == sub.endpoint
    payload = json.loads(args[4])
    assert payload["kind"] == "other" and payload["url"] == "/settings" and payload["title"]
    sub.refresh_from_db()
    assert sub.last_used_at is not None

    PushSubscription.objects.update(notify_other=False)
    with mock.patch("apps.push.tasks.send_push.delay") as delay:
        notify(alice, "security", gettext_noop("Новий вхід"))
    assert delay.call_count == 0


@pytest.mark.django_db
def test_notify_without_vapid_or_inactive_user(alice, settings):
    from apps.core.realtime import notify

    settings.VAPID_PUBLIC_KEY = ""
    _subscribe(alice)
    with mock.patch("apps.push.tasks.send_push.delay") as delay:
        notify(alice, "security", gettext_noop("Новий вхід"))
    assert delay.call_count == 0


def _direct(alice, bob):
    from apps.chat.models import Conversation, Participant

    conv = Conversation.objects.create(created_by=alice)
    Participant.objects.create(conversation=conv, user=alice)
    pb = Participant.objects.create(conversation=conv, user=bob)
    return conv, pb


@pytest.mark.django_db
def test_chat_message_pushes_to_other_participants(alice, bob, client_for, vapid):
    alice.display_name = "Аліса"
    alice.save()
    conv, pb = _direct(alice, bob)
    _subscribe(alice)
    bob_sub = _subscribe(bob)
    url = f"/api/chat/conversations/{conv.pk}/messages/"
    with mock.patch("apps.push.tasks.send_push.delay") as delay:
        assert client_for(alice).post(url, {"body": "Привіт! " + "x" * 200}, format="json").status_code == 201
    assert delay.call_count == 1 and delay.call_args.args[0] == str(bob_sub.pk)
    assert delay.call_args.kwargs["urgency"] == "high"
    payload = json.loads(delay.call_args.args[4])
    assert payload["title"] == "Аліса" and payload["kind"] == "chat"
    assert payload["body"].startswith("Привіт! ") and len(payload["body"]) == 101
    assert payload["url"] == f"/chat/{conv.pk}"

    # Без попереднього перегляду — ні відправника, ні тексту
    PushSubscription.objects.filter(pk=bob_sub.pk).update(show_previews=False)
    with mock.patch("apps.push.tasks.send_push.delay") as delay:
        client_for(alice).post(url, {"body": "секрет"}, format="json")
    payload = json.loads(delay.call_args.args[4])
    assert "секрет" not in json.dumps(payload, ensure_ascii=False) and "Аліса" not in payload["title"]

    # Розмову без звуку — не турбуємо; вимкнений чат — теж
    pb.muted = True
    pb.save()
    with mock.patch("apps.push.tasks.send_push.delay") as delay:
        client_for(alice).post(url, {"body": "ще"}, format="json")
    assert delay.call_count == 0


@pytest.mark.django_db
def test_chat_attachment_and_group_titles(alice, bob, vapid):
    from types import SimpleNamespace

    from apps.chat.models import Conversation, Participant
    from apps.push.service import notify_chat_message

    conv = Conversation.objects.create(created_by=alice, is_group=True, title="Команда")
    Participant.objects.create(conversation=conv, user=alice)
    Participant.objects.create(conversation=conv, user=bob)
    _subscribe(bob)
    with mock.patch("apps.push.tasks.send_push.delay") as delay:
        notify_chat_message(conv, SimpleNamespace(body=""), alice)
    payload = json.loads(delay.call_args.args[4])
    assert payload["title"] == "Команда" and payload["body"].startswith("alice: 📎")


# ─────────────────────── Нові листи (опитування IMAP) ───────────────────────


class FakeInbox:
    """IMAP у пам'яті: STATUS UIDNEXT/UIDVALIDITY, SELECT, UID FETCH заголовків."""

    validity = 7
    messages: dict = {}
    next_uid = 1

    @classmethod
    def reset(cls):
        cls.messages, cls.next_uid, cls.validity = {}, 1, 7

    @classmethod
    def deliver(cls, sender, subject, seen=False):
        cls.messages[cls.next_uid] = (sender, subject, seen)
        cls.next_uid += 1

    def status(self, mailbox, items):
        assert mailbox == '"INBOX"'
        return "OK", [f'"INBOX" (UIDNEXT {self.next_uid} UIDVALIDITY {self.validity})'.encode()]

    def select(self, mailbox, readonly=False):
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, cmd, rng, what):
        assert cmd == "FETCH" and "BODY.PEEK" in what
        lo, hi = (int(x) for x in rng.split(":"))
        out = []
        for uid in range(lo, hi + 1):
            if uid in self.messages:
                sender, subject, seen = self.messages[uid]
                flags = "\\Seen" if seen else ""
                hdr = f"From: {sender}\r\nSubject: {subject}\r\n\r\n".encode()
                out += [(f"{uid} (UID {uid} FLAGS ({flags}) BODY[HEADER.FIELDS (FROM SUBJECT)] {{{len(hdr)}}}".encode(), hdr), b")"]
        return "OK", out


@pytest.fixture
def inbox(monkeypatch):
    from contextlib import contextmanager

    from apps.mail import imap

    FakeInbox.reset()
    opened = []

    @contextmanager
    def session(address):
        opened.append(address)
        yield FakeInbox()

    monkeypatch.setattr(imap, "imap_session", session)
    FakeInbox.opened = opened
    return FakeInbox


@pytest.mark.django_db
def test_mail_poll_pushes_new_unseen_messages(alice, bob, vapid, inbox):
    from apps.push.mailwatch import poll

    sub = _subscribe(alice)
    inbox.deliver("Old <old@example.org>", "давнє")
    with mock.patch("apps.push.tasks.send_push.delay") as delay:
        assert poll() == 0  # перший прохід лише запам'ятовує стан
        inbox.deliver("=?utf-8?b?0IbQstCw0L0=?= <ivan@example.org>", "Звіт за жовтень")
        inbox.deliver("me@example.org", "вже прочитано", seen=True)
        inbox.deliver("noname@example.org", "")
        assert poll() == 2
        assert poll() == 0  # повторно не надсилається
    assert inbox.opened == ["alice@example.com"] * 3  # bob без підписок не перевіряється
    payloads = [json.loads(c.args[4]) for c in delay.call_args_list]
    assert [p["title"] for p in payloads] == ["Іван", "noname@example.org"]
    assert payloads[0]["body"] == "Звіт за жовтень" and payloads[0]["url"] == "/mail" and payloads[0]["kind"] == "mail"
    assert payloads[1]["body"] == "(без теми)"
    assert all(c.args[0] == str(sub.pk) for c in delay.call_args_list)


@pytest.mark.django_db
def test_mail_poll_limits_and_uidvalidity(alice, vapid, inbox):
    from apps.push.mailwatch import poll

    _subscribe(alice)
    with mock.patch("apps.push.tasks.send_push.delay") as delay:
        poll()
        for i in range(8):
            inbox.deliver(f"s{i}@example.org", f"лист {i}")
        assert poll() == 5  # не більше 5 за прохід — найновіші
        assert json.loads(delay.call_args.args[4])["body"] == "лист 7"
        inbox.validity = 8  # скриньку перебудовано — новий базовий стан без сповіщень
        inbox.deliver("x@example.org", "x")
        assert poll() == 0


@pytest.mark.django_db
def test_mail_poll_skips_when_mail_disabled_or_unavailable(alice, vapid, monkeypatch):
    from contextlib import contextmanager

    from apps.mail import imap
    from apps.push.mailwatch import poll

    _subscribe(alice, notify_mail=False)
    calls = []

    @contextmanager
    def broken(address):
        calls.append(address)
        raise imap.MailUnavailable()
        yield

    monkeypatch.setattr(imap, "imap_session", broken)
    assert poll() == 0 and calls == []
    _subscribe(alice, n=1)
    assert poll() == 0 and calls == ["alice@example.com"]  # помилка не ламає завдання


# ─────────────────────── Надсилання (pusher) ───────────────────────


class FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(status_codes, requests):
    codes = iter(status_codes)

    def open_(request, timeout=None):
        requests.append(request)
        code = next(codes)
        if code >= 400:
            raise urllib.error.HTTPError(request.full_url, code, "err", {}, None)
        return FakeResponse(code)

    return open_


@pytest.mark.django_db
def test_send_push_success_headers(alice, vapid, monkeypatch):
    from apps.push.tasks import send_push

    sub = _subscribe(alice)
    requests = []
    monkeypatch.setattr(webpush._opener, "open", _opener([201], requests))
    r = send_push.apply(args=(str(sub.pk), sub.endpoint, sub.p256dh, sub.auth, '{"title":"t"}'), kwargs={"urgency": "high"})
    assert r.get() == "ok"
    req = requests[0]
    headers = {k.lower(): v for k, v in req.header_items()}
    assert req.get_method() == "POST" and req.full_url == sub.endpoint
    assert headers["content-encoding"] == "aes128gcm" and headers["ttl"] == "86400" and headers["urgency"] == "high"
    assert headers["authorization"].startswith("vapid t=") and f"k={webpush.public_key_b64(vapid)}" in headers["authorization"]
    jwt = headers["authorization"].split("t=")[1].split(",")[0]
    assert json.loads(d(jwt.split(".")[1]))["aud"] == "https://fcm.googleapis.com"
    assert len(req.data) == 86 + len('{"title":"t"}') + 1 + 16


@pytest.mark.django_db
@pytest.mark.parametrize("code", [404, 410])
def test_send_push_gone_deletes_subscription(alice, vapid, monkeypatch, code):
    from apps.push.tasks import send_push

    sub, other = _subscribe(alice), _subscribe(alice, n=1)
    monkeypatch.setattr(webpush._opener, "open", _opener([code], []))
    assert send_push.apply(args=(str(sub.pk), sub.endpoint, sub.p256dh, sub.auth, "{}")).get() == "gone"
    assert list(PushSubscription.objects.values_list("pk", flat=True)) == [other.pk]


@pytest.mark.django_db
def test_send_push_retries_then_gives_up(alice, vapid, monkeypatch):
    from apps.push.tasks import send_push

    sub = _subscribe(alice)
    requests = []
    monkeypatch.setattr(webpush._opener, "open", _opener([429, 503, 500, 502, 200], requests))
    result = send_push.apply(args=(str(sub.pk), sub.endpoint, sub.p256dh, sub.auth, "{}"))
    assert len(requests) == 4  # перша спроба + 3 повтори
    assert result.get() == "failed"
    assert PushSubscription.objects.filter(pk=sub.pk).exists()


@pytest.mark.django_db
def test_send_push_rejected_and_invalid(alice, vapid, monkeypatch, settings):
    from apps.push.tasks import send_push

    sub = _subscribe(alice)
    monkeypatch.setattr(webpush._opener, "open", _opener([400], []))
    assert send_push.apply(args=(str(sub.pk), sub.endpoint, sub.p256dh, sub.auth, "{}")).get() == "rejected"
    assert PushSubscription.objects.filter(pk=sub.pk).exists()
    # Підроблена адреса в черзі (не з дозволеного списку) — не надсилається, підписка видаляється
    opened = []
    monkeypatch.setattr(webpush._opener, "open", _opener([201], opened))
    assert send_push.apply(args=(str(sub.pk), "https://evil.example.com/", sub.p256dh, sub.auth, "{}")).get() == "invalid"
    assert opened == [] and not PushSubscription.objects.filter(pk=sub.pk).exists()
    settings.VAPID_PRIVATE_KEY = ""
    assert send_push.apply(args=("x", sub.endpoint, sub.p256dh, sub.auth, "{}")).get() == "disabled"


def test_payload_is_bounded():
    from apps.push.service import build_payload

    raw = build_payload("chat", "т" * 500, "ж" * 5000, "https://evil.example/", "tag")
    data = json.loads(raw)
    assert len(raw.encode()) <= 3000 and data["url"] == "/" and len(data["title"]) == 120
