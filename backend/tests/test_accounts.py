import time

import pyotp
import pytest
from rest_framework.test import APIClient

from apps.accounts import twofactor
from apps.core.crypto import decrypt_str


def login(client, login_value, password):
    return client.post("/api/auth/login/", {"login": login_value, "password": password}, format="json")


@pytest.mark.django_db
def test_login_by_username_and_email(alice, password):
    c = APIClient()
    assert login(c, "alice", password).json()["status"] == "ok"
    c2 = APIClient()
    assert login(c2, "ALICE@example.org", password).json()["status"] == "ok"


@pytest.mark.django_db
def test_wrong_password_is_generic_and_locks_out(alice, password):
    c = APIClient()
    for _ in range(5):
        r = login(c, "alice", "wrong-password")
        assert r.status_code == 400
        assert "Невірний логін або пароль" in r.json()["detail"]
    # навіть правильний пароль тепер блокується
    assert login(c, "alice", password).status_code == 429


@pytest.mark.django_db
def test_unknown_user_same_error(db):
    r = login(APIClient(), "nobody", "whatever-123456")
    assert r.status_code == 400
    assert "Невірний логін або пароль" in r.json()["detail"]


def _enable_totp(user):
    secret, _, _ = twofactor.start_totp_setup(user)
    user.refresh_from_db()
    code = pyotp.TOTP(secret).now()
    codes = twofactor.confirm_totp(user, code)
    assert codes and len(codes) == 10
    user.refresh_from_db()
    return secret, codes


@pytest.mark.django_db
def test_totp_secret_encrypted_at_rest(alice):
    secret, _ = _enable_totp(alice)
    stored = alice.totp_device.secret_encrypted
    assert secret not in stored
    assert decrypt_str(stored) == secret


@pytest.mark.django_db
def test_login_requires_second_factor(alice, password):
    secret, _ = _enable_totp(alice)
    c = APIClient()
    r = login(c, "alice", password)
    assert r.json()["status"] == "2fa_required"
    # до проходження 2FA користувач не автентифікований
    assert c.get("/api/auth/me/").json()["authenticated"] is False
    assert c.get("/api/files/browse/").status_code in (401, 403)
    # TOTP-код вже використаний при підтвердженні в цьому ж кроці часу → replay
    r = c.post("/api/auth/login/2fa/", {"code": pyotp.TOTP(secret).now()}, format="json")
    if r.status_code == 400:
        # чекаємо наступний крок часу
        time.sleep(31 - (time.time() % 30))
        r = c.post("/api/auth/login/2fa/", {"code": pyotp.TOTP(secret).now()}, format="json")
    assert r.json()["status"] == "ok"
    assert c.get("/api/auth/me/").json()["authenticated"] is True


@pytest.mark.django_db
def test_totp_replay_rejected(alice):
    secret, _ = _enable_totp(alice)
    device = alice.totp_device
    code = pyotp.TOTP(secret).at(time.time() + 30)
    assert twofactor.verify_totp(device, code) is True
    assert twofactor.verify_totp(device, code) is False


@pytest.mark.django_db
def test_backup_code_single_use(alice, password):
    _, codes = _enable_totp(alice)
    assert twofactor.verify_second_factor(alice, codes[0]) == "backup_code"
    assert twofactor.verify_second_factor(alice, codes[0]) is None


@pytest.mark.django_db
def test_2fa_attempts_limited(alice, password):
    _enable_totp(alice)
    c = APIClient()
    login(c, "alice", password)
    for _ in range(5):
        assert c.post("/api/auth/login/2fa/", {"code": "000000"}, format="json").status_code == 400
    assert c.post("/api/auth/login/2fa/", {"code": "000000"}, format="json").status_code == 429


@pytest.mark.django_db
def test_registration_closed_without_invite(db, settings):
    settings.REGISTRATION_OPEN = False
    r = APIClient().post(
        "/api/auth/register/",
        {"username": "eve", "email": "eve@example.org", "password": "Another-Strong-Pass1"},
        format="json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_invite_registration_single_use(alice, settings):
    from apps.accounts.models import Invite

    settings.REGISTRATION_OPEN = False
    _, token = Invite.issue(created_by=alice)
    data = {"username": "eve", "email": "eve@example.org", "password": "Another-Strong-Pass1", "invite": token}
    assert APIClient().post("/api/auth/register/", data, format="json").status_code == 201
    data.update(username="mallory", email="m@example.org")
    assert APIClient().post("/api/auth/register/", data, format="json").status_code == 400


@pytest.mark.django_db
def test_admin_api_requires_2fa_for_staff(make_user, client_for, settings):
    settings.REQUIRE_2FA_FOR_STAFF = True
    admin = make_user("root1", is_staff=True)
    assert client_for(admin).get("/api/admin/users/").status_code == 403
    _enable_totp(admin)
    # 2FA увімкнена, але ця сесія не проходила другий фактор → доступу немає
    assert client_for(admin).get("/api/admin/users/").status_code == 403
    assert client_for(admin, mfa=True).get("/api/admin/users/").status_code == 200


@pytest.mark.django_db
def test_non_staff_cannot_use_admin_api(alice, client_for):
    assert client_for(alice).get("/api/admin/users/").status_code == 403


@pytest.mark.django_db
def test_quota_reservation_is_atomic(alice):
    alice.quota_bytes = 100
    alice.used_bytes = 0
    alice.save()
    assert alice.try_reserve_bytes(60) is True
    assert alice.try_reserve_bytes(60) is False
    assert alice.try_reserve_bytes(40) is True
    alice.release_bytes(1000)
    alice.refresh_from_db()
    assert alice.used_bytes == 0
