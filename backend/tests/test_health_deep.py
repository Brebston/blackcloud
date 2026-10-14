"""Глибока перевірка стану /api/health/deep/ для моніторингу."""

from collections import namedtuple
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from rest_framework.test import APIClient

from apps.core import views

URL = "/api/health/deep/"
TOKEN = "t0ken-for-tests-1234567890"
DiskUsage = namedtuple("DiskUsage", "total used free")


def _der_cert(days_valid: float) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cloud.example.com")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days_valid))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.DER)


@pytest.fixture
def healthy(settings, monkeypatch):
    """Усе зовнішнє — замоканий «здоровий» стан."""
    settings.HEALTH_TOKEN = TOKEN
    settings.DOMAIN = "cloud.example.com"
    settings.IS_LOCAL_DOMAIN = False
    settings.MAIL_HOSTNAME = "mail.example.com"
    settings.HEALTH_DISK_ALERT_PERCENT = 85
    settings.HEALTH_CERT_MIN_DAYS = 14
    calls = []

    def fake_peer(host, port, server_name):
        calls.append((host, port, server_name))
        return _der_cert(60)

    monkeypatch.setattr(views, "_peer_cert_der", fake_peer)
    monkeypatch.setattr(views.shutil, "disk_usage", lambda p: DiskUsage(100 * 1024**3, 50 * 1024**3, 50 * 1024**3))
    from apps.storage import scanner

    monkeypatch.setattr(scanner, "status", lambda: {"enabled": True, "reachable": True, "version": "x", "error": ""})
    from apps.core.tasks import celery_heartbeat

    celery_heartbeat()
    return calls


def _get(token=TOKEN):
    c = APIClient()
    if token is None:
        return c.get(URL)
    return c.get(URL, HTTP_AUTHORIZATION=f"Bearer {token}")


@pytest.mark.django_db
def test_disabled_without_token(settings):
    settings.HEALTH_TOKEN = ""
    assert _get("anything").status_code == 404


@pytest.mark.django_db
def test_wrong_or_missing_token(healthy):
    assert _get("wrong").status_code == 403
    assert _get(None).status_code == 403
    r = APIClient().get(URL, HTTP_AUTHORIZATION=f"Basic {TOKEN}")
    assert r.status_code == 403


@pytest.mark.django_db
def test_all_ok(healthy):
    r = _get()
    assert r.status_code == 200, r.json()
    data = r.json()
    assert data["status"] == "ok"
    assert set(data["checks"]) == {"db", "cache", "clamav", "disk", "cert_web", "cert_mail", "celery", "backup"}
    # резервні копії не налаштовані (немає status.json) — перевірка пропускається, а не падає
    assert data["checks"]["backup"] == {"ok": True, "skipped": True}
    assert data["checks"]["disk"]["percent"] == 50.0
    assert data["checks"]["disk"]["free_gb"] == 50.0
    assert 58 <= data["checks"]["cert_web"]["days_left"] <= 60
    # Сайт — через Traefik з SNI домену, пошта — Dovecot IMAPS з SNI поштового хоста
    assert ("172.31.250.2", 443, "cloud.example.com") in healthy
    assert ("dovecot", 993, "mail.example.com") in healthy


@pytest.mark.django_db
def test_disk_full(healthy, monkeypatch):
    monkeypatch.setattr(views.shutil, "disk_usage", lambda p: DiskUsage(100, 90, 10))
    r = _get()
    assert r.status_code == 503
    assert r.json()["status"] == "fail"
    assert r.json()["checks"]["disk"]["ok"] is False
    assert r.json()["checks"]["disk"]["percent"] == 90.0


@pytest.mark.django_db
def test_clamav_unreachable(healthy, monkeypatch):
    from apps.storage import scanner

    monkeypatch.setattr(
        scanner, "status", lambda: {"enabled": True, "reachable": False, "version": "", "error": "clamd at 10.0.0.1"}
    )
    r = _get()
    assert r.status_code == 503
    assert r.json()["checks"]["clamav"] == {"ok": False, "enabled": True}
    assert "10.0.0.1" not in r.content.decode()


@pytest.mark.django_db
def test_clamav_disabled_is_ok(healthy, monkeypatch):
    from apps.storage import scanner

    monkeypatch.setattr(scanner, "status", lambda: {"enabled": False, "reachable": False, "version": "", "error": ""})
    assert _get().status_code == 200


@pytest.mark.django_db
def test_cert_expiring(healthy, monkeypatch):
    monkeypatch.setattr(views, "_peer_cert_der", lambda h, p, n: _der_cert(5))
    r = _get()
    assert r.status_code == 503
    assert r.json()["checks"]["cert_web"]["ok"] is False
    assert r.json()["checks"]["cert_mail"]["ok"] is False


@pytest.mark.django_db
def test_cert_connection_error_hides_details(healthy, monkeypatch):
    def boom(host, port, name):
        raise ConnectionRefusedError("secret-host:443 refused")

    monkeypatch.setattr(views, "_peer_cert_der", boom)
    r = _get()
    assert r.status_code == 503
    assert r.json()["checks"]["cert_web"] == {"ok": False, "reason": "ConnectionRefusedError"}
    assert "secret-host" not in r.content.decode()


@pytest.mark.django_db
def test_cert_checks_skipped_for_localhost(healthy, settings, monkeypatch):
    settings.IS_LOCAL_DOMAIN = True
    settings.MAIL_HOSTNAME = ""
    monkeypatch.setattr(views, "_peer_cert_der", lambda *a: pytest.fail("не має підключатися"))
    r = _get()
    assert r.status_code == 200
    assert r.json()["checks"]["cert_web"] == {"ok": True, "skipped": True}
    assert r.json()["checks"]["cert_mail"] == {"ok": True, "skipped": True}


@pytest.mark.django_db
def test_celery_heartbeat_missing_or_stale(healthy, monkeypatch):
    from django.core.cache import cache

    cache.delete(views.CELERY_HEARTBEAT_KEY)
    r = _get()
    assert r.status_code == 503
    assert r.json()["checks"]["celery"] == {"ok": False, "reason": "no_heartbeat"}

    cache.set(views.CELERY_HEARTBEAT_KEY, views.time.time() - 600, 60)
    r = _get()
    assert r.status_code == 503
    assert r.json()["checks"]["celery"]["ok"] is False


@pytest.mark.django_db
def test_unexpected_error_in_check(healthy, monkeypatch):
    def broken():
        raise RuntimeError("password=hunter2")

    monkeypatch.setitem(views.HEALTH_CHECKS, "db", broken)
    r = _get()
    assert r.status_code == 503
    assert r.json()["checks"]["db"] == {"ok": False, "reason": "error"}
    assert "hunter2" not in r.content.decode()


@pytest.mark.django_db
def test_throttled(healthy):
    codes = [_get("wrong").status_code for _ in range(31)]
    assert codes[-1] == 429


def test_peer_cert_der_real_tls(tmp_path):
    """Справжнє TLS-рукостискання з локальним сервером (без перевірки ланцюжка)."""
    import socket
    import ssl
    import threading

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    (tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "k.pem").write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tmp_path / "c.pem", tmp_path / "k.pem")
    srv = socket.create_server(("127.0.0.1", 0))
    port = srv.getsockname()[1]

    def serve():
        conn, _ = srv.accept()
        try:
            with ctx.wrap_socket(conn, server_side=True) as tls:
                tls.recv(1)
        except (OSError, ssl.SSLError):
            pass

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    der = views._peer_cert_der("127.0.0.1", port, "cloud.example.com")
    srv.close()
    days, _ = views._cert_days_left(der)
    assert 29 <= days <= 30
