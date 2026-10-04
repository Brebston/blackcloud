import uuid

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models

DOMAIN_VALIDATOR = RegexValidator(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", "Невірний домен")
LOCAL_PART_VALIDATOR = RegexValidator(r"^[a-z0-9][a-z0-9._+-]{0,63}$", "Невірна локальна частина адреси")


class MailDomain(models.Model):
    name = models.CharField(max_length=253, unique=True, validators=[DOMAIN_VALIDATOR])
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = self.name.lower()
        super().save(*args, **kwargs)


class Mailbox(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mailbox")
    domain = models.ForeignKey(MailDomain, on_delete=models.PROTECT, related_name="mailboxes")
    local_part = models.CharField(max_length=64, validators=[LOCAL_PART_VALIDATOR])
    quota_mb = models.PositiveIntegerField(default=2048)
    active = models.BooleanField(default=True)
    # Окремий пароль для поштових клієнтів (IMAP/SMTP), у форматі Dovecot: {ARGON2ID}$argon2id$...
    # Пароль акаунта для пошти НЕ використовується — клієнти не підтримують 2FA.
    client_password_hash = models.CharField(max_length=255, blank=True)
    client_password_set_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["domain", "local_part"], name="uniq_mailbox_address")]

    @property
    def address(self) -> str:
        return f"{self.local_part}@{self.domain.name}"

    def __str__(self):
        return self.address


class Alias(models.Model):
    source = models.EmailField(unique=True)
    destination = models.EmailField()
    active = models.BooleanField(default=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="mail_aliases")

    class Meta:
        verbose_name_plural = "aliases"

    def save(self, *args, **kwargs):
        self.source = self.source.lower()
        self.destination = self.destination.lower()
        super().save(*args, **kwargs)
