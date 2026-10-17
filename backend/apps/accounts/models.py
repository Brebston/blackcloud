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
from django.utils.translation import gettext_lazy

USERNAME_VALIDATOR = RegexValidator(
    r"^[a-z0-9][a-z0-9._-]{2,39}$",
    gettext_lazy("Ім'я користувача: 3–40 символів, малі латинські літери, цифри, '.', '_', '-'."),
)


# Імена, з якими можна видати себе за службу або отримати службову пошту
# (postmaster@, abuse@, ssl-admin@ — на них ЦС надсилають листи підтвердження домену).
RESERVED_USERNAMES = {
    "admin", "administrator", "root", "system", "sysadmin", "superuser", "staff", "moderator",
    "postmaster", "hostmaster", "webmaster", "abuse", "security", "noreply", "no-reply",
    "mailer-daemon", "daemon", "nobody", "webmail", "mail", "smtp", "imap", "pop", "pop3",
    "support", "help", "helpdesk", "info", "contact", "billing", "sales", "office", "usercontent",
    "ssl-admin", "ssladmin", "ssladministrator", "admin-ssl", "dmarc", "dkim", "spf", "bounce",
    "tlsrpt", "mta-sts",
    "bounces", "rspamd", "dovecot", "postfix", "clamav", "blackcloud", "api", "www", "ftp",
    "privacy", "legal", "team", "it", "owner", "test", "verify", "verification", "accounts",
}


def _normalize_name(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


_RESERVED_NORMALIZED = {_normalize_name(n) for n in RESERVED_USERNAMES}


def is_reserved_username(value: str) -> bool:
    """«Ad.min», «post_master», «security-1»? Порівнюємо без '.', '-', '_' і хвостових цифр."""
    norm = _normalize_name(value)
    return norm in _RESERVED_NORMALIZED or norm.rstrip("0123456789") in _RESERVED_NORMALIZED


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

    # Фото профілю: 0 — немає; інакше номер версії (для URL і ETag). Самі байти — в UserAvatar.
    avatar_version = models.PositiveIntegerField(default=0)

    # Блокування адміністратором (is_active=False + причина). Помилка входу лишається загальною.
    blocked_reason = models.CharField(max_length=500, blank=True)
    blocked_at = models.DateTimeField(null=True, blank=True)
    blocked_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

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
        """Код з додатка (TOTP) увімкнено."""
        device = getattr(self, "totp_device", None)
        return bool(device and device.confirmed)

    @property
    def has_passkeys(self) -> bool:
        return self.passkeys.exists()

    @property
    def has_strong_auth(self) -> bool:
        """Є спосіб входу з другим фактором: TOTP або ключ доступу (passkey з біометрією/PIN)."""
        return self.has_2fa or self.has_passkeys

    @property
    def avatar_url(self) -> str | None:
        if not self.avatar_version:
            return None
        return f"/api/users/{self.pk}/avatar/?v={self.avatar_version}"

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
    # Акцентні кольори інтерфейсу (значення кольорів — у frontend/src/styles.css)
    ACCENTS = [
        ("violet", "Фіолетовий"),
        ("blue", "Синій"),
        ("teal", "Бірюзовий"),
        ("green", "Зелений"),
        ("amber", "Бурштиновий"),
        ("orange", "Помаранчевий"),
        ("rose", "Рожевий"),
        ("slate", "Графітовий"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="preferences", primary_key=True)
    language = models.CharField(max_length=5, choices=LANGS, default="uk")
    timezone = models.CharField(max_length=64, default="Europe/Kyiv")
    theme = models.CharField(max_length=10, choices=THEMES, default="system")
    accent = models.CharField(max_length=12, choices=ACCENTS, default="violet")
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


class Passkey(models.Model):
    """Ключ доступу (WebAuthn/FIDO2): Face ID / Touch ID / Windows Hello / апаратний ключ.

    Сервер зберігає лише публічний ключ; приватний ніколи не залишає пристрій або
    менеджер паролів (iCloud Keychain, Google, 1Password…). Вхід вимагає перевірки
    користувача на пристрої (біометрія або PIN), тож це вже два фактори."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="passkeys")
    credential_id = models.BinaryField(unique=True)
    public_key = models.BinaryField()
    sign_count = models.PositiveBigIntegerField(default=0)
    transports = models.JSONField(default=list, blank=True)
    aaguid = models.CharField(max_length=36, blank=True)
    # Синхронізується між пристроями (iCloud Keychain, Google Password Manager)
    backed_up = models.BooleanField(default=False)
    name = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.user} · {self.name}"


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
    # Лист із запрошенням: коли надіслано востаннє, скільки разів, код останньої помилки
    sent_at = models.DateTimeField(null=True, blank=True)
    send_count = models.PositiveSmallIntegerField(default=0)
    send_error = models.CharField(max_length=40, blank=True)

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

    def rotate_token(self) -> str:
        """Новий токен (старе посилання перестає працювати) — для повторного надсилання."""
        token = secrets.token_urlsafe(32)
        self.token_hash = self.hash_token(token)
        self.save(update_fields=["token_hash"])
        return token

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()


class UserAvatar(models.Model):
    """Фото профілю.

    Завантажений оригінал (до 5 МБ) зберігається зашифрованим (Fernet) лише до обробки:
    декодування недовірених зображень виконує ізольований сервіс thumbnailer (через worker),
    а не веб-процес. Готове фото — квадрат 256×256 WebP без EXIF — невелике, тож лежить у БД.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="avatar", primary_key=True)
    image = models.BinaryField(blank=True, default=b"")
    content_type = models.CharField(max_length=40, blank=True)
    # Оригінал, що чекає на обробку: Fernet-токен; pending_id захищає від гонок двох завантажень
    pending = models.BinaryField(blank=True, default=b"")
    pending_id = models.UUIDField(null=True, blank=True)
    failed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class AccountDeletion(models.Model):
    """Видалення акаунта, заплановане адміністратором (див. accounts/deletion.py).

    До purge_after акаунт заблокований, а видалення можна скасувати. Після виконання рядок
    лишається з user=NULL: ім'я й поштові адреси ще ACCOUNT_NAME_HOLD_DAYS не видаються
    іншим — щоб листи, адресовані колишньому власнику, не потрапили новому."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="deletion")
    username = models.CharField(max_length=40, db_index=True)
    addresses = models.JSONField(default=list, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    requested_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    requested_at = models.DateTimeField(auto_now_add=True)
    purge_after = models.DateTimeField(db_index=True)
    # Не NULL лише під час стирання: поки так, Dovecot бачить скриньки (щоб стерти листи)
    started_at = models.DateTimeField(null=True, blank=True)
    purged_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return self.username
