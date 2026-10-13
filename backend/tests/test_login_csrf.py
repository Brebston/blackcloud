"""Регресійні тести «login CSRF»: анонімні запити, що створюють або змінюють
сесію, мають вимагати CSRF-токен (аудит безпеки, пункт 1)."""

import pytest
from rest_framework.test import APIClient


def _csrf_client():
    c = APIClient(enforce_csrf_checks=True)
    token = c.get("/api/auth/csrf/").json()["csrfToken"]
    return c, token


@pytest.mark.django_db
@pytest.mark.parametrize("fmt", ["json", "multipart"])
def test_login_without_csrf_token_is_rejected(alice, password, fmt):
    c = APIClient(enforce_csrf_checks=True)
    r = c.post("/api/auth/login/", {"login": "alice", "password": password}, format=fmt)
    assert r.status_code == 403
    assert "CSRF" in r.content.decode()
    assert c.get("/api/auth/me/").json()["authenticated"] is False


@pytest.mark.django_db
def test_login_from_foreign_origin_is_rejected(alice, password):
    c, token = _csrf_client()
    r = c.post(
        "/api/auth/login/",
        {"login": "alice", "password": password},
        format="json",
        HTTP_X_CSRFTOKEN=token,
        HTTP_ORIGIN="https://evil.example",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_login_with_csrf_token_succeeds(alice, password):
    c, token = _csrf_client()
    r = c.post(
        "/api/auth/login/",
        {"login": "alice", "password": password},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert r.status_code == 200, r.content
    assert c.get("/api/auth/me/").json()["authenticated"] is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url,data",
    [
        ("/api/auth/login/2fa/", {"code": "000000"}),
        ("/api/auth/register/", {"username": "eve", "email": "eve@example.org", "password": "Another-Strong-Pass1"}),
        ("/api/auth/logout/", {}),
    ],
)
def test_other_session_endpoints_require_csrf(db, url, data):
    r = APIClient(enforce_csrf_checks=True).post(url, data, format="json")
    assert r.status_code == 403
    assert "CSRF" in r.content.decode()


@pytest.mark.django_db
def test_csrf_endpoint_and_me_stay_open():
    c = APIClient(enforce_csrf_checks=True)
    assert c.get("/api/auth/csrf/").status_code == 200
    assert c.get("/api/auth/me/").status_code == 200
