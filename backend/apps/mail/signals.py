import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.accounts.models import User

from .models import MailDomain, Mailbox

logger = logging.getLogger("blackcloud")


@receiver(post_save, sender=User)
def create_mailbox(sender, instance, created, **kwargs):
    if not created or not settings.MAIL_AUTO_CREATE:
        return
    try:
        with transaction.atomic():
            domain, _ = MailDomain.objects.get_or_create(name=settings.MAIL_DOMAIN.lower())
            Mailbox.objects.create(
                user=instance, domain=domain, local_part=instance.username, quota_mb=settings.MAIL_DEFAULT_QUOTA_MB
            )
    except IntegrityError:
        logger.warning("mailbox for %s already exists or address taken", instance.username)
