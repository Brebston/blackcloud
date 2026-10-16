"""Адмінка → «Стан сервера» і /api/health/deep/?only=… для окремих моніторів Kuma."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core import views, views_status
from apps.storage.models import File

from .test_accounts import _enable_totp
from .test_health_deep import TOKEN, DiskUsage, healthy  # noqa: F401 — фікстура
from .test_storage import upload

URL = "/api/admin/status/"
MEMINFO = {"MemTotal": 16 * 1024**3, "MemAvailable": 6 * 1024**3, "SwapTotal": 2 * 1024**3, "SwapFree": 2 * 1024**3}


@pytest.fixture
def admin_client(make_user, client_for, settings, healthy, monkeypatch):  # noqa: F811
    settings.REQUIRE_2FA_FOR_STAFF = True
    settings.BACKUP_STATE_DIR = "/nonexistent-backup-state"
    monkeypatch.setattr(views_status, "_read_meminfo", lambda: dict(MEMINFO))
    u = make_user("chief", is_staff=True, is_superuser=True)
    _enable_totp(u)
    return client_for(u, mfa=True)


@pytest.mark.django_db
def test_status_all_good(admin_client):
    r = admin_client.get(URL)
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and d["problems"] == []
    assert d["disk"]["percent"] == 50.0 and d["disk"]["threshold"] == 85
    assert d["memory"]["percent"] == 62.5 and d["memory"]["total"] == 16 * 1024**3
    assert d["certs"]["web"]["ok"] and d["certs"]["web"]["days_left"] >= 59
    assert d["backup"] == {"ok": True, "configured": False}
    assert d["antivirus"]["pending"] == 0 and d["antivirus"]["reachable"] is True
    assert set(d["services"]) == {"db", "cache", "celery"}


@pytest.mark.django_db
def test_status_reports_problems(admin_client, monkeypatch):
    monkeypatch.setattr(views.shutil, "disk_usage", lambda p: DiskUsage(100, 90, 10))
    monkeypatch.setattr(views_status, "_read_meminfo", lambda: {"MemTotal": 1000, "MemAvailable": 50})
    d = admin_client.get(URL).json()
    assert d["ok"] is False
    assert {"disk", "memory"} <= set(d["problems"])
    assert d["disk"]["percent"] == 90.0 and d["memory"]["percent"] == 95.0


@pytest.mark.django_db
def test_status_stuck_antivirus(admin_client, alice, client_for):
    info = upload(client_for(alice), b"hello", "a.txt")
    File.objects.filter(pk=info["id"]).update(status=File.Status.SCANNING, updated_at=timezone.now() - timedelta(hours=1))
    d = admin_client.get(URL).json()
    assert d["antivirus"]["pending"] == 1 and d["antivirus"]["oldest_minutes"] >= 59
    assert "antivirus" in d["problems"]


@pytest.mark.django_db
def test_status_one_broken_block_does_not_break_page(admin_client, monkeypatch):
    def boom():
        raise OSError("no /proc")

    monkeypatch.setattr(views_status, "_read_meminfo", boom)
    r = admin_client.get(URL)
    assert r.status_code == 200
    assert r.json()["memory"] == {"ok": False, "reason": "OSError"}


@pytest.mark.django_db
def test_certs_cached_between_refreshes(admin_client, healthy):  # noqa: F811
    admin_client.get(URL)
    n = len(healthy)
    admin_client.get(URL)
    assert len(healthy) == n  # з кешу, без нових TLS-з'єднань
    admin_client.get(URL + "?refresh=1")
    assert len(healthy) > n


@pytest.mark.django_db
def test_status_only_staff_with_2fa(make_user, client_for, settings):
    settings.REQUIRE_2FA_FOR_STAFF = True
    assert client_for(make_user("bob")).get(URL).status_code == 403
    staff = make_user("helper", is_staff=True)
    assert client_for(staff, mfa=True).get(URL).status_code == 403
    assert APIClient().get(URL).status_code in (401, 403)


@pytest.mark.django_db
def test_health_deep_only_disk(healthy, monkeypatch):  # noqa: F811
    def deep(q):
        return APIClient().get(f"/api/health/deep/{q}", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")

    r = deep("?only=disk")
    assert r.status_code == 200 and list(r.json()["checks"]) == ["disk"]
    monkeypatch.setattr(views.shutil, "disk_usage", lambda p: DiskUsage(100, 86, 14))
    assert deep("?only=disk").status_code == 503
    assert deep("?only=disk,nope").status_code == 400
    # Без only — як раніше, усі перевірки
    assert set(deep("").json()["checks"]) == set(views.HEALTH_CHECKS)
