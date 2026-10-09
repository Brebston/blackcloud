from pathlib import Path

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from apps.core.i18n import language_from_header

MO = Path(settings.BASE_DIR) / "locale" / "en" / "LC_MESSAGES" / "django.mo"
needs_catalog = pytest.mark.skipif(not MO.exists(), reason="англійський каталог ще не скомпільовано")


def _bad_login(**extra):
    return APIClient().post("/api/auth/login/", {"login": "nobody", "password": "whatever-123456"}, format="json", **extra)


@pytest.mark.django_db
def test_login_error_ukrainian_by_default():
    r = _bad_login()
    assert r.status_code == 400
    assert r.json()["detail"] == "Невірний логін або пароль."


@needs_catalog
@pytest.mark.django_db
def test_login_error_in_english():
    r = _bad_login(HTTP_ACCEPT_LANGUAGE="en")
    assert r.status_code == 400
    assert r.json()["detail"] == "Invalid username or password."


@pytest.mark.django_db
def test_login_error_ukrainian_when_requested():
    r = _bad_login(HTTP_ACCEPT_LANGUAGE="uk")
    assert r.json()["detail"] == "Невірний логін або пароль."


def test_language_from_header():
    assert language_from_header(b"en-US,en;q=0.9") == "en"
    assert language_from_header("uk") == "uk"
    assert language_from_header("de") == settings.LANGUAGE_CODE
    assert language_from_header(None) == settings.LANGUAGE_CODE
