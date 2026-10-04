import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone

USERNAME_VALIDATOR = RegexValidator(
    r"^[a-z0-9][a-z0-9._-]{2,39}$",
    "Ім'я користувача: 3–40 символів, малі латинські літери, цифри, '.', '_', '-'.",
)


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, username, email, password, **extra):
        if not username:
            raise ValueError("Потрібне ім'я користувача")
        if not email:
            raise ValueError("Потрібен email")
        user = self.model(username=username.lower(), email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_user(self, username, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(username, email, password, **extra)

    def create_superuser(self, username, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True)
        return self._create_user(username, email, password, **extra)


def default_quota():
    return settings.DEFAULT_QUOTA_BYTES


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=40, unique=True, validators=[USERNAME_VALIDATOR])
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    password_changed_at = models.DateTimeField(null=True, blank=True)

    # Квота файлового сховища
    quota_bytes = models.BigIntegerField(default=default_quota)
    used_bytes = models.BigIntegerField(default=0)

    objects = UserManager()

    USERNAME_FIELD = "username"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["email"]

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(used_bytes__gte=0), name="user_used_bytes_non_negative"),
            models.CheckConstraint(condition=models.Q(quota_bytes__gte=0), name="user_quota_non_negative"),
        ]

    def __str__(self):
        return self.username

    @property
    def has_2fa(self) -> bool:
        device = getattr(self, "totp_device", None)
        return bool(device and device.confirmed)

    def set_password(self, raw_password):
        super().set_password(raw_password)
        self.password_changed_at = timezone.now()

    # ─── Квоти: атомарне резервування місця ───────────────────────
    def try_reserve_bytes(self, amount: int) -> bool:
        """Атомарно резервує місце. Повертає False, якщо квоту перевищено.

        Одна SQL-операція UPDATE ... WHERE used + amount <= quota, тому
        паралельні завантаження не можуть обійти ліміт.
        """
        if amount < 0:
            raise ValueError("amount must be >= 0")
        updated = User.objects.filter(pk=self.pk, used_bytes__lte=F("quota_bytes") - amount).update(
            used_bytes=F("used_bytes") + amount
        )
        return updated == 1

    def release_bytes(self, amount: int) -> None:
        if amount <= 0:
            return
        # Greatest(..., 0) — захист від від'ємних значень при розсинхронізації
        User.objects.filter(pk=self.pk).update(used_bytes=Greatest(F("used_bytes") - amount, 0))


class Preferences(models.Model):
    THEMES = [("system", "Системна"), ("dark", "Темна"), ("light", "Світла")]
    LANGS = [("uk", "Українська"), ("en", "English")]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="preferences", primary_key=True)
    language = models.CharField(max_length=5, choices=LANGS, default="uk")
    timezone = models.CharField(max_length=64, default="Europe/Kyiv")
    theme = models.CharField(max_length=10, choices=THEMES, default="system")
    discoverable = models.BooleanField(default=True, help_text="Інші користувачі можуть знайти мене в пошуку")
    notify_login_email = models.BooleanField(default=True)
    mail_load_remote_images = models.BooleanField(default=False)
    week_starts_monday = models.BooleanField(default=True)


class TOTPDevice(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="totp_device")
    secret_encrypted = models.TextField()
    confirmed = models.BooleanField(default=False)
    last_used_step = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)


class BackupCode(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="backup_codes")
    code_hash = models.CharField(max_length=64)
    used_at = models.DateTimeField(null=True, blank=True)

    @staticmethod
    def hash_code(code: str) -> str:
        normalized = code.replace("-", "").replace(" ", "").lower()
        return hashlib.sha256(f"{settings.SECRET_KEY}:{normalized}".encode()).hexdigest()


class UserSession(models.Model):
    """Метадані активних сесій: для списку пристроїв і віддаленого виходу."""

    session_key = models.CharField(max_length=40, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tracked_sessions")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(default=timezone.now)


def _invite_expiry():
    return timezone.now() + timedelta(days=7)


class Invite(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token_hash = models.CharField(max_length=64, unique=True)
    email = models.EmailField(blank=True, help_text="Якщо задано — запрошення лише для цієї адреси")
    quota_bytes = models.BigIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=_invite_expiry)
    used_by = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    used_at = models.DateTimeField(null=True, blank=True)

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @classmethod
    def issue(cls, *, created_by, email="", quota_bytes=None, days=7):
        token = secrets.token_urlsafe(32)
        invite = cls.objects.create(
            token_hash=cls.hash_token(token),
            email=email.lower(),
            quota_bytes=quota_bytes,
            created_by=created_by,
            expires_at=timezone.now() + timedelta(days=days),
        )
        return invite, token

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()
