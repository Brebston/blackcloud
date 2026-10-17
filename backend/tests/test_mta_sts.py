import io

import pytest
from django.core.management import call_command
from django.test import Client

from apps.mail import mta_sts


@pytest.fixture
def sts(settings):
    settings.MAIL_DOMAIN = "example.com"
    settings.MAIL_HOSTNAME = "mail.example.com"
    settings.MTA_STS_MODE = "testing"
    settings.MTA_STS_HOST = "mta-sts.example.com"
    settings.MTA_STS_MX = ["mail.example.com"]
    settings.MTA_STS_MAX_AGE = 86400
    settings.TLSRPT_RUA = "mailto:tlsrpt@example.com"
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "mta-sts.example.com", "example.com"]
    return settings


def get_policy(host):
    return Client().get("/.well-known/mta-sts.txt", HTTP_HOST=host, secure=True)


@pytest.mark.django_db
def test_policy_served_on_mta_sts_host(sts):
    r = get_policy("mta-sts.example.com")
    assert r.status_code == 200
    assert r["Content-Type"].startswith("text/plain")
    assert r.content.decode() == "version: STSv1\r\nmode: testing\r\nmx: mail.example.com\r\nmax_age: 86400\r\n"


@pytest.mark.django_db
def test_policy_only_on_its_host_and_when_enabled(sts):
    assert get_policy("example.com").status_code == 404
    sts.MTA_STS_MODE = ""
    assert get_policy("mta-sts.example.com").status_code == 404


@pytest.mark.django_db
def test_enforce_multiple_mx_and_none(sts):
    sts.MTA_STS_MODE = "enforce"
    sts.MTA_STS_MX = ["mail.example.com", "*.backup.example.com"]
    sts.MTA_STS_MAX_AGE = 604800
    body = get_policy("mta-sts.example.com").content.decode()
    assert "mode: enforce\r\n" in body
    assert "mx: mail.example.com\r\nmx: *.backup.example.com\r\n" in body
    assert "max_age: 604800" in body
    sts.MTA_STS_MODE = "none"
    assert "mode: none\r\n" in get_policy("mta-sts.example.com").content.decode()


def test_policy_id_changes_with_policy(sts):
    first = mta_sts.policy_id()
    assert first.isalnum() and 1 <= len(first) <= 32
    assert mta_sts.policy_id() == first
    sts.MTA_STS_MODE = "enforce"
    assert mta_sts.policy_id() != first


def test_dns_command_prints_records(sts):
    out = io.StringIO()
    call_command("mta_sts_dns", stdout=out)
    text = out.getvalue()
    assert f'_mta-sts.example.com    "v=STSv1; id={mta_sts.policy_id()}"' in text
    assert '_smtp._tls.example.com    "v=TLSRPTv1; rua=mailto:tlsrpt@example.com"' in text
    assert "mta-sts.example.com" in text
