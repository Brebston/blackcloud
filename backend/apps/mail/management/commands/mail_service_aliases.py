"""Створює службові адреси postmaster@, abuse@, dmarc@ … як псевдоніми на скриньку адміністратора.

RFC 5321 вимагає, щоб postmaster@домен існував; abuse@ перевіряють списки блокування,
а на dmarc@ приходять звіти DMARC. Звичайні користувачі ці імена зареєструвати не можуть.

    python manage.py mail_service_aliases --to yurii
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.mail.models import Alias, Mailbox

SERVICE_NAMES = ["postmaster", "abuse", "hostmaster", "webmaster", "dmarc", "security", "noreply"]


class Command(BaseCommand):
    help = "Псевдоніми postmaster@/abuse@/dmarc@… на скриньку адміністратора"

    def add_arguments(self, parser):
        parser.add_argument("--to", required=True, help="ім'я користувача, чия скринька отримуватиме листи")

    def handle(self, *args, to, **options):
        mailbox = Mailbox.objects.select_related("domain", "user").filter(user__username=to.lower()).first()
        if mailbox is None:
            raise CommandError(f"У користувача {to} немає поштової скриньки")
        domain = settings.MAIL_DOMAIN.lower()
        for name in SERVICE_NAMES:
            alias, created = Alias.objects.update_or_create(
                source=f"{name}@{domain}", defaults={"destination": mailbox.address, "active": True}
            )
            self.stdout.write(f"{'+' if created else '='} {alias.source} -> {alias.destination}")
