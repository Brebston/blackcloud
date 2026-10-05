import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Folder(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="folders")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["owner", "parent"])]

    def __str__(self):
        return self.name


class File(models.Model):
    class Status(models.TextChoices):
        UPLOADING = "uploading", "Завантажується"
        SCANNING = "scanning", "Перевірка антивірусом"
        CLEAN = "clean", "Готовий"
        INFECTED = "infected", "Заражений (карантин)"
        UNSCANNED = "unscanned", "Не перевірено (завеликий)"
        FAILED = "failed", "Помилка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="files")
    folder = models.ForeignKey(Folder, null=True, blank=True, on_delete=models.CASCADE, related_name="files")
    name = models.CharField(max_length=255)
    size = models.BigIntegerField()
    mime_type = models.CharField(max_length=127, default="application/octet-stream")
    sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.UPLOADING, db_index=True)
    scan_detail = models.CharField(max_length=200, blank=True)

    # Параметри шифрування та чанкового завантаження
    wrapped_key = models.BinaryField()
    key_version = models.PositiveSmallIntegerField(default=1)
    chunk_size = models.PositiveIntegerField()
    chunks_received = models.PositiveIntegerField(default=0)
    upload_expires_at = models.DateTimeField(null=True, blank=True)
    # Версія вмісту: збільшується при кожному збереженні з онлайн-редактора
    content_version = models.PositiveIntegerField(default=0)
    has_thumbnail = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["owner", "folder"]), models.Index(fields=["owner", "status"])]
        constraints = [models.CheckConstraint(condition=Q(size__gte=0), name="file_size_non_negative")]

    def __str__(self):
        return self.name

    @property
    def chunks_total(self) -> int:
        if self.size == 0:
            return 0
        return (self.size + self.chunk_size - 1) // self.chunk_size

    def expected_chunk_len(self, index: int) -> int:
        if index < self.chunks_total - 1:
            return self.chunk_size
        return self.size - self.chunk_size * (self.chunks_total - 1)

    @property
    def object_prefix(self) -> str:
        return f"files/{self.id}/"

    def version_prefix(self, version: int | None = None) -> str:
        version = self.content_version if version is None else version
        return self.object_prefix if version == 0 else f"{self.object_prefix}v{version}/"

    def chunk_key(self, index: int, version: int | None = None) -> str:
        return f"{self.version_prefix(version)}{index:06d}"

    def thumb_key(self, version: int | None = None) -> str:
        version = self.content_version if version is None else version
        return f"{self.object_prefix}thumb-v{version}"

    @property
    def extension(self) -> str:
        return self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""

    @property
    def is_downloadable(self) -> bool:
        if self.status == self.Status.CLEAN:
            return True
        return self.status == self.Status.UNSCANNED and settings.UNSCANNED_POLICY == "allow"


class Share(models.Model):
    """Внутрішній доступ іншому користувачу (лише читання)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="outgoing_shares")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="incoming_shares")
    file = models.ForeignKey(File, null=True, blank=True, on_delete=models.CASCADE, related_name="shares")
    folder = models.ForeignKey(Folder, null=True, blank=True, on_delete=models.CASCADE, related_name="shares")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(file__isnull=False) & Q(folder__isnull=True)) | (Q(file__isnull=True) & Q(folder__isnull=False)),
                name="share_exactly_one_target",
            ),
            models.UniqueConstraint(fields=["recipient", "file"], name="uniq_share_file"),
            models.UniqueConstraint(fields=["recipient", "folder"], name="uniq_share_folder"),
        ]


class PublicLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="public_links")
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name="public_links")
    token_hash = models.CharField(max_length=64, unique=True)
    password_hash = models.CharField(max_length=200, blank=True)
    expires_at = models.DateTimeField()
    max_downloads = models.PositiveIntegerField(null=True, blank=True)
    download_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def new_token() -> str:
        return secrets.token_urlsafe(32)

    @property
    def is_active(self) -> bool:
        if self.revoked_at or self.expires_at <= timezone.now():
            return False
        if self.max_downloads is not None and self.download_count >= self.max_downloads:
            return False
        return True
