"""Ключі доступу (WebAuthn): реєстрація, вхід без пароля, другий фактор, захист.

Програмний автентифікатор нижче підписує відповіді справжнім ключем P-256 так само, як
Face ID / Touch ID / YubiKey, тож перевірка проходить повністю (py_webauthn), без моків."""

import hashlib
import json
import os
import struct
import time

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from rest_framework.test import APIClient
from webauthn.helpers import bytes_to_base64url

from apps.accounts.models import Passkey
from apps.core.models import AuditLog, Notification

from .test_accounts import _enable_totp

RP_ID = "cloud.example.com"
ORIGIN = f"https://{RP_ID}"


class SoftAuthenticator:
    def __init__(self, *, origin=ORIGIN, rp_id=RP_ID, uv=True, counter=False):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(16)
        self.origin, self.rp_id, self.uv, self.counter = origin, rp_id, uv, counter
        self.count = 0
        self.user_handle = None

    def _cose(self) -> bytes:
        n = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})

    def _auth_data(self, attested=False) -> bytes:
        flags = 0x01 | (0x04 if self.uv else 0) | (0x40 if attested else 0)
        data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", self.count)
        if attested:
            data += bytes(16) + struct.pack(">H", len(self.cred_id)) + self.cred_id + self._cose()
        return data

    def _client(self, kind, challenge) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": self.origin, "crossOrigin": False}).encode()

    def create(self, options: dict) -> dict:
        self.user_handle = options["user"]["id"]
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": self._auth_data(attested=True)})
        b = bytes_to_base64url
        return {
            "id": b(self.cred_id),
            "rawId": b(self.cred_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b(self._client("webauthn.create", options["challenge"])),
                "attestationObject": b(att),
                "transports": ["internal", "hybrid"],
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }

    def get(self, options: dict, *, user_handle=None) -> dict:
        if self.counter:
            self.count += 1
        client = self._client("webauthn.get", options["challenge"])
        ad = self._auth_data()
        sig = self.key.sign(ad + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        b = bytes_to_base64url
        return {
            "id": b(self.cred_id),
            "rawId": b(self.cred_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b(client),
                "authenticatorData": b(ad),
                "signature": b(sig),
                "userHandle": user_handle if user_handle is not None else self.user_handle,
            },
            "clientExtensionResults": {},
        }


@pytest.fixture(autouse=True)
def rp(settings, monkeypatch):
    settings.WEBAUTHN_RP_ID = RP_ID
    settings.WEBAUTHN_ORIGIN = ORIGIN
    # Тести роблять десятки входів за секунди: загальний ліміт DRF (10/хв) тут заважав би
    # перевірити власне блокування ключів доступу за IP
    from rest_framework.throttling import ScopedRateThrottle

    for scope in ("login", "two_factor", "passkey_register"):
        monkeypatch.setitem(ScopedRateThrottle.THROTTLE_RATES, scope, "1000/min")


def password_login(username, password) -> APIClient:
    c = APIClient()
    r = c.post("/api/auth/login/", {"login": username, "password": password}, format="json")
    assert r.status_code == 200, r.content
    return c


def add_passkey(c, auth=None, **begin) -> SoftAuthenticator:
    auth = auth or SoftAuthenticator()
    r = c.post("/api/account/security/passkeys/register/begin/", begin, format="json")
    assert r.status_code == 200, r.content
    cred = auth.create(r.json()["options"])
    r = c.post("/api/account/security/passkeys/register/finish/", {"credential": cred, "name": "Mac"}, format="json")
    assert r.status_code == 201, r.content
    return auth


def passkey_login(auth, **kw):
    c = APIClient()
    opts = c.post("/api/auth/login/passkey/begin/").json()["options"]
    r = c.post("/api/auth/login/passkey/finish/", {"credential": auth.get(opts, **kw)}, format="json")
    return c, r


# ─────────────────────────── Реєстрація ───────────────────────────


@pytest.mark.django_db
def test_register_right_after_login_without_password(alice, password):
    c = password_login("alice", password)
    r = c.post("/api/account/security/passkeys/register/begin/", {"platform": True}, format="json")
    opts = r.json()["options"]
    assert opts["rp"]["id"] == RP_ID
    assert opts["authenticatorSelection"]["residentKey"] == "required"
    assert opts["authenticatorSelection"]["userVerification"] == "required"
    assert opts["authenticatorSelection"]["authenticatorAttachment"] == "platform"
    auth = SoftAuthenticator()
    r = c.post("/api/account/security/passkeys/register/finish/", {"credential": auth.create(opts)}, format="json")
    assert r.status_code == 201
    pk = Passkey.objects.get(user=alice)
    assert bytes(pk.credential_id) == auth.cred_id and pk.transports == ["internal", "hybrid"]
    assert AuditLog.objects.filter(action="passkey.added").exists()
    assert Notification.objects.filter(user=alice, kind="security").exists()
    # повторно той самий ключ: в exclude_credentials і відхиляється при спробі
    opts = c.post("/api/account/security/passkeys/register/begin/", {}, format="json").json()["options"]
    assert opts["excludeCredentials"][0]["id"] == bytes_to_base64url(auth.cred_id)
    r = c.post("/api/account/security/passkeys/register/finish/", {"credential": auth.create(opts)}, format="json")
    assert r.status_code == 400


@pytest.mark.django_db
def test_register_later_requires_password(alice, password, settings):
    c = password_login("alice", password)
    settings.PASSKEY_FRESH_LOGIN_SECONDS = -1  # «вхід був давно»
    r = c.post("/api/account/security/passkeys/register/begin/", {}, format="json")
    assert r.status_code == 400 and "password" in r.json()
    r = c.post("/api/account/security/passkeys/register/begin/", {"password": "wrong"}, format="json")
    assert r.status_code == 400
    add_passkey(c, password=password)


@pytest.mark.django_db
def test_register_rejects_wrong_origin_and_no_user_verification(alice, password):
    c = password_login("alice", password)
    for auth in (SoftAuthenticator(origin="https://evil.example"), SoftAuthenticator(uv=False)):
        opts = c.post("/api/account/security/passkeys/register/begin/", {}, format="json").json()["options"]
        r = c.post("/api/account/security/passkeys/register/finish/", {"credential": auth.create(opts)}, format="json")
        assert r.status_code == 400
    assert not Passkey.objects.exists()


# ─────────────────────────── Вхід без пароля ───────────────────────────


@pytest.mark.django_db
def test_passwordless_login_is_mfa(alice, password):
    auth = add_passkey(password_login("alice", password))
    c = APIClient()
    opts = c.post("/api/auth/login/passkey/begin/").json()["options"]
    assert opts["allowCredentials"] == [] and opts["userVerification"] == "required"
    r = c.post("/api/auth/login/passkey/finish/", {"credential": auth.get(opts)}, format="json")
    assert r.status_code == 200, r.content
    assert c.get("/api/auth/me/").json()["user"]["username"] == "alice"
    assert c.session["mfa"] is True
    assert Passkey.objects.get(user=alice).last_used_at is not None
    assert AuditLog.objects.filter(action="login.success", metadata__method="passkey").exists()


@pytest.mark.django_db
def test_staff_with_only_passkey_gets_admin(make_user, password, settings):
    settings.REQUIRE_2FA_FOR_STAFF = True
    make_user("chief", is_staff=True)
    c = password_login("chief", password)
    assert c.get("/api/auth/me/").json()["user"]["must_enroll_2fa"] is True
    assert c.get("/api/admin/users/").status_code == 403  # лише пароль
    auth = add_passkey(c)
    c2, r = passkey_login(auth)
    assert r.status_code == 200
    assert c2.get("/api/auth/me/").json()["user"]["must_enroll_2fa"] is False
    assert c2.get("/api/admin/users/").status_code == 200


@pytest.mark.django_db
def test_login_rejects_replay_bad_signature_wrong_handle_and_blocked(alice, bob, password):
    auth = add_passkey(password_login("alice", password))
    # повтор тієї самої відповіді: виклик одноразовий
    c = APIClient()
    opts = c.post("/api/auth/login/passkey/begin/").json()["options"]
    cred = auth.get(opts)
    assert c.post("/api/auth/login/passkey/finish/", {"credential": cred}, format="json").status_code == 200
    c2 = APIClient()
    c2.post("/api/auth/login/passkey/begin/")
    assert c2.post("/api/auth/login/passkey/finish/", {"credential": cred}, format="json").status_code == 400
    # підробка: чужий ключ із тим самим credential id
    fake = SoftAuthenticator()
    fake.cred_id, fake.user_handle = auth.cred_id, auth.user_handle
    assert passkey_login(fake)[1].status_code == 400
    # userHandle іншого користувача
    other = bytes_to_base64url(bob.pk.bytes)
    assert passkey_login(auth, user_handle=other)[1].status_code == 400
    # заблокований акаунт
    alice.is_active = False
    alice.save()
    assert passkey_login(auth)[1].status_code == 400


@pytest.mark.django_db
def test_sign_counter_regression_rejected(alice, password):
    auth = add_passkey(password_login("alice", password), SoftAuthenticator(counter=True))
    assert passkey_login(auth)[1].status_code == 200
    auth.count = 0  # «клон» зі старим лічильником (get додасть 1 → не більше збереженого)
    assert passkey_login(auth)[1].status_code == 400


@pytest.mark.django_db
def test_ip_lockout_after_failures(alice, password):
    auth = add_passkey(password_login("alice", password))
    stranger = SoftAuthenticator()
    for _ in range(20):
        passkey_login(stranger)
    # 20 невдач з цієї IP — блокування на 15 хв, навіть для справжнього ключа
    c = APIClient()
    assert c.post("/api/auth/login/passkey/begin/").status_code == 429
    r = c.post("/api/auth/login/passkey/finish/", {"credential": auth.get({"challenge": "x"})}, format="json")
    assert r.status_code == 429


# ─────────────────────────── Вибір способу входу ───────────────────────────


@pytest.mark.django_db
def test_password_only_still_works_with_passkey(alice, password):
    add_passkey(password_login("alice", password))
    r = APIClient().post("/api/auth/login/", {"login": "alice", "password": password}, format="json")
    assert r.json()["status"] == "ok"  # без TOTP ключ доступу — додатковий спосіб, а не обов'язковий


@pytest.mark.django_db
def test_passkey_as_second_factor_after_password(alice, bob, password):
    auth = add_passkey(password_login("alice", password))
    _enable_totp(alice)
    c = APIClient()
    r = c.post("/api/auth/login/", {"login": "alice", "password": password}, format="json")
    assert r.json() == {"status": "2fa_required", "passkey": True}
    opts = c.post("/api/auth/login/2fa/passkey/begin/").json()["options"]
    assert [a["id"] for a in opts["allowCredentials"]] == [bytes_to_base64url(auth.cred_id)]
    r = c.post("/api/auth/login/2fa/passkey/finish/", {"credential": auth.get(opts)}, format="json")
    assert r.status_code == 200
    assert c.get("/api/auth/me/").json()["authenticated"] is True and c.session["mfa"] is True

    # ключ іншого користувача не підходить як другий фактор для alice
    bob_auth = add_passkey(password_login("bob", password))
    c = APIClient()
    c.post("/api/auth/login/", {"login": "alice", "password": password}, format="json")
    opts = c.post("/api/auth/login/2fa/passkey/begin/").json()["options"]
    r = c.post("/api/auth/login/2fa/passkey/finish/", {"credential": bob_auth.get(opts)}, format="json")
    assert r.status_code == 400
    assert c.get("/api/auth/me/").json()["authenticated"] is False


@pytest.mark.django_db
def test_second_factor_needs_pending_password_step(alice, password):
    add_passkey(password_login("alice", password))
    assert APIClient().post("/api/auth/login/2fa/passkey/begin/").status_code == 400


# ─────────────────────────── Керування ───────────────────────────


@pytest.mark.django_db
def test_list_rename_delete(alice, bob, password):
    c = password_login("alice", password)
    add_passkey(c)
    items = c.get("/api/account/security/passkeys/").json()
    assert len(items) == 1 and items[0]["name"] == "Mac"
    pid = items[0]["id"]
    assert c.patch(f"/api/account/security/passkeys/{pid}/", {"name": "MacBook Air"}, format="json").json()["name"] == "MacBook Air"
    # чужий ключ не видно
    assert password_login("bob", password).patch(f"/api/account/security/passkeys/{pid}/", {"name": "x"}, format="json").status_code == 404
    assert c.delete(f"/api/account/security/passkeys/{pid}/", {"password": "wrong"}, format="json").status_code == 400
    assert c.delete(f"/api/account/security/passkeys/{pid}/", {"password": password}, format="json").status_code == 204
    assert not Passkey.objects.exists()
    sec = c.get("/api/account/security/").json()
    assert sec["passkeys"] == 0 and sec["has_strong_auth"] is False


@pytest.mark.django_db
def test_anonymous_cannot_manage():
    c = APIClient()
    assert c.get("/api/account/security/passkeys/").status_code in (401, 403)
    assert c.post("/api/account/security/passkeys/register/begin/").status_code in (401, 403)


@pytest.mark.django_db
def test_challenge_expires(alice, password, monkeypatch):
    auth = add_passkey(password_login("alice", password))
    c = APIClient()
    opts = c.post("/api/auth/login/passkey/begin/").json()["options"]
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 600)
    r = c.post("/api/auth/login/passkey/finish/", {"credential": auth.get(opts)}, format="json")
    assert r.status_code == 400
