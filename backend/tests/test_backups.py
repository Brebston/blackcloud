"""Адмінка → резервні копії: стан зі status.json і запит «зробити копію зараз» (файл-прапорець)."""

import json
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import AuditLog

from .test_accounts import _enable_totp
from .test_health_deep import TOKEN, healthy  # noqa: F401 — фікстура


@pytest.fixture
def state(tmp_path, settings):
    settings.BACKUP_STATE_DIR = str(tmp_path)
    (tmp_path / "status").mkdir()
    (tmp_path / "request").mkdir()
    return tmp_path


def write_status(state, **overrides):
    data = {
        "version": 1,
        "last_backup": {"status": "ok", "started_at": "x", "finished_at": "y", "duration_s": 42, "trigger": "schedule"},
        "last_success_at": (timezone.now() - timedelta(hours=3)).isoformat(),
        "snapshot_count": 1,
        "snapshots": [{"id": "ab12cd34", "time": "2026-10-14T03:30:12+02:00", "data_added": 1024}],
        "repo_size_bytes": 3_600_000_000,
    }
    data.update(overrides)
    (state / "status" / "status.json").write_text(json.dumps(data))


@pytest.fixture
def admin_client(make_user, client_for, settings):
    settings.REQUIRE_2FA_FOR_STAFF = True
    u = make_user("chief", is_staff=True, is_superuser=True)
    _enable_totp(u)
    return client_for(u, mfa=True)


@pytest.mark.django_db
def test_not_configured(admin_client, settings, tmp_path):
    settings.BACKUP_STATE_DIR = str(tmp_path / "missing")
    r = admin_client.get("/api/admin/backups/")
    assert r.status_code == 200
    assert r.json()["configured"] is False and r.json()["can_request"] is False
    assert admin_client.post("/api/admin/backups/run/").status_code == 503


@pytest.mark.django_db
def test_status_shown(admin_client, state):
    write_status(state)
    body = admin_client.get("/api/admin/backups/").json()
    assert body["configured"] is True and body["stale"] is False and body["running"] is False
    assert body["status"]["snapshots"][0]["id"] == "ab12cd34"
    assert body["status"]["repo_size_bytes"] == 3_600_000_000

    write_status(state, last_success_at=(timezone.now() - timedelta(hours=30)).isoformat())
    assert admin_client.get("/api/admin/backups/").json()["stale"] is True


@pytest.mark.django_db
def test_run_request_creates_flag_once(admin_client, state):
    write_status(state)
    r = admin_client.post("/api/admin/backups/run/")
    assert r.status_code == 202
    flag = state / "request" / "run"
    assert json.loads(flag.read_text())["user"] == "chief"
    assert AuditLog.objects.filter(action="backup.requested").count() == 1
    assert admin_client.get("/api/admin/backups/").json()["request_pending"] is True
    # повторний запит, поки попередній не підхопив systemd — 409
    assert admin_client.post("/api/admin/backups/run/").status_code == 409


@pytest.mark.django_db
def test_run_refused_while_running(admin_client, state):
    write_status(state, last_backup={"status": "running", "started_at": timezone.now().isoformat()})
    assert admin_client.post("/api/admin/backups/run/").status_code == 409
    assert not (state / "request" / "run").exists()
    # «завислий» стан (старіший за 6 год) кнопку не блокує
    old = (timezone.now() - timedelta(hours=7)).isoformat()
    write_status(state, last_backup={"status": "running", "started_at": old})
    assert admin_client.post("/api/admin/backups/run/").status_code == 202


@pytest.mark.django_db
def test_only_staff_with_2fa(make_user, client_for, state, settings):
    write_status(state)
    settings.REQUIRE_2FA_FOR_STAFF = True
    user = make_user("bob")
    assert client_for(user).get("/api/admin/backups/").status_code == 403
    assert client_for(user).post("/api/admin/backups/run/").status_code == 403
    staff_no_2fa = make_user("helper", is_staff=True)
    assert client_for(staff_no_2fa, mfa=True).post("/api/admin/backups/run/").status_code == 403
    assert APIClient().get("/api/admin/backups/").status_code in (401, 403)
    assert not (state / "request" / "run").exists()


@pytest.mark.django_db
def test_garbage_status_ignored(admin_client, state):
    (state / "status" / "status.json").write_text("{not json")
    assert admin_client.get("/api/admin/backups/").json()["configured"] is False


@pytest.mark.django_db
def test_health_deep_backup_check(healthy, state):  # noqa: F811
    def deep():
        return APIClient().get("/api/health/deep/", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")

    write_status(state)
    r = deep()
    assert r.status_code == 200 and r.json()["checks"]["backup"]["ok"] is True

    write_status(state, last_success_at=(timezone.now() - timedelta(hours=30)).isoformat())
    r = deep()
    assert r.status_code == 503 and r.json()["checks"]["backup"] == {"ok": False, "age_hours": 30}
