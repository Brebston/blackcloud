"""MTA-STS (RFC 8461) і TLS-RPT (RFC 8460).

Політика віддається за адресою https://mta-sts.<MAIL_DOMAIN>/.well-known/mta-sts.txt —
лише на цьому хості й лише коли MTA_STS_MODE задано. Відправники (Gmail, Outlook…) беруть її,
якщо в DNS є TXT `_mta-sts.<домен>` з `v=STSv1; id=…`; id мусить змінюватися при кожній зміні
політики, тому він обчислюється з її вмісту (policy_id). Записи DNS показує
`python manage.py mta_sts_dns` (`make mta-sts`).
"""

import hashlib

from django.conf import settings
from django.http import Http404, HttpResponse


def policy_text() -> str:
    """Текст політики: пари «ключ: значення», розділені CRLF (RFC 8461 §3.2)."""
    lines = ["version: STSv1", f"mode: {settings.MTA_STS_MODE or 'none'}"]
    lines += [f"mx: {mx}" for mx in settings.MTA_STS_MX]
    lines.append(f"max_age: {settings.MTA_STS_MAX_AGE}")
    return "\r\n".join(lines) + "\r\n"


def policy_id() -> str:
    """Ідентифікатор для TXT _mta-sts (1–32 літери/цифри): змінюється разом із політикою."""
    return hashlib.sha256(policy_text().encode()).hexdigest()[:20]


def dns_records() -> list[tuple[str, str, str]]:
    domain = settings.MAIL_DOMAIN.lower()
    return [
        ("TXT", f"_mta-sts.{domain}", f"v=STSv1; id={policy_id()}"),
        ("TXT", f"_smtp._tls.{domain}", f"v=TLSRPTv1; rua={settings.TLSRPT_RUA}"),
    ]


def mta_sts_policy(request):
    host = request.get_host().split(":", 1)[0].lower()
    if not settings.MTA_STS_MODE or host != settings.MTA_STS_HOST:
        raise Http404()
    response = HttpResponse(policy_text(), content_type="text/plain; charset=utf-8")
    # Відправники самі кешують політику на max_age; HTTP-кеш короткий, щоб зміни швидко діяли
    response["Cache-Control"] = "public, max-age=300"
    return response
