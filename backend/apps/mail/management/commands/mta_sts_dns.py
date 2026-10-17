"""DNS-записи для MTA-STS і TLS-RPT та поточна політика.

    python manage.py mta_sts_dns      (make mta-sts)
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.mail import mta_sts


class Command(BaseCommand):
    help = "Показати DNS-записи MTA-STS / TLS-RPT і текст політики"

    def handle(self, *args, **options):
        out = self.stdout.write
        if not settings.MTA_STS_MODE:
            out("MTA_STS_MODE не задано — політика вимкнена. Додайте в .env MTA_STS_MODE=testing")
            out("(спершу створіть A-запис нижче, інакше Let's Encrypt не видасть сертифікат).")
            out("")
        out("DNS-записи:")
        out(f"  A    {settings.MTA_STS_HOST}    <IP сервера>   (Cloudflare: «DNS only», сіра хмаринка)")
        for kind, name, value in mta_sts.dns_records():
            out(f"  {kind}  {name}    \"{value}\"")
        out("")
        out(f"Політика (https://{settings.MTA_STS_HOST}/.well-known/mta-sts.txt):")
        for line in mta_sts.policy_text().splitlines():
            out(f"  {line}")
        out("")
        out("Після будь-якої зміни MTA_STS_* оновіть TXT _mta-sts: id має змінитися (відправники інакше")
        out("тримають стару політику до max_age).")
