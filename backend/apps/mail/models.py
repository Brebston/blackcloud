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
    """Поштова скринька. Користувач може мати кілька скриньок (створює адміністратор)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mailboxes")
    domain = models.ForeignKey(MailDomain, on_delete=models.PROTECT, related_name="mailboxes")
    local_part = models.CharField(max_length=64, validators=[LOCAL_PART_VALIDATOR])
    # Ім'я відправника в полі From і назва скриньки в інтерфейсі
    display_name = models.CharField(max_length=100, blank=True)
    # Каталог листів на диску (Dovecot). Не залежить від адреси, тож скриньку можна
    # перейменувати без переміщення листів. Для старих скриньок дорівнює local_part.
    maildir = models.CharField(max_length=64, blank=True, editable=False)
    quota_mb = models.PositiveIntegerField(default=2048)
    active = models.BooleanField(default=True)
    # Окремий пароль для поштових клієнтів (IMAP/SMTP), у форматі Dovecot: {ARGON2ID}$argon2id$...
    # Пароль акаунта для пошти НЕ використовується — клієнти не підтримують 2FA.
    client_password_hash = models.CharField(max_length=255, blank=True)
    client_password_set_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["domain", "local_part"], name="uniq_mailbox_address"),
            models.UniqueConstraint(fields=["domain", "maildir"], name="uniq_mailbox_maildir"),
        ]

    @property
    def address(self) -> str:
        return f"{self.local_part}@{self.domain.name}"

    def save(self, *args, **kwargs):
        self.local_part = self.local_part.lower()
        if not self.maildir:
            # Нові скриньки зберігаються за id: перейменування іншої скриньки
            # ніколи не призведе до спільного каталогу
            self.maildir = self.id.hex
        super().save(*args, **kwargs)

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


class Signature(models.Model):
    """Підпис відправника (HTML після санітизації)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mail_signatures")
    name = models.CharField(max_length=60)
    html = models.TextField(max_length=200_000)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]


class ConfidentialMessage(models.Model):
    """Конфіденційний лист: вміст не надсилається поштою, а зберігається зашифрованим
    на сервері. Отримувач бачить лише посилання; доступ закінчується в заданий термін
    або після відкликання відправником."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="confidential_messages")
    from_address = models.CharField(max_length=320)
    recipients = models.TextField(blank=True)
    subject = models.CharField(max_length=300, blank=True)
    html_encrypted = models.TextField(blank=True)
    token_hash = models.CharField(max_length=64, unique=True)
    passcode_hash = models.CharField(max_length=200, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    view_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_active(self) -> bool:
        from django.utils import timezone

        return self.revoked_at is None and self.expires_at > timezone.now() and bool(self.html_encrypted)
