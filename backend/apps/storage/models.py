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
    # Велике зображення для перегляду форматів, які браузер не показує сам (HEIC, TIFF…)
    has_preview = models.BooleanField(default=False)
    # Хто і коли записав поточний вміст (для історії версій); null — власник / created_at
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    content_updated_at = models.DateTimeField(null=True, blank=True)
    # Останній виданий номер версії вмісту (номери ніколи не використовуються повторно)
    version_counter = models.PositiveIntegerField(default=0)

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

    def preview_key(self, version: int | None = None) -> str:
        version = self.content_version if version is None else version
        return f"{self.object_prefix}preview-v{version}"

    @property
    def extension(self) -> str:
        return self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""

    @property
    def is_downloadable(self) -> bool:
        if self.status == self.Status.CLEAN:
            return True
        return self.status == self.Status.UNSCANNED and settings.UNSCANNED_POLICY == "allow"


class FileVersion(models.Model):
    """Попередня (неактуальна) версія вмісту файлу або незавершене завантаження нової версії.

    Поточна версія завжди описується самим File; тут — лише архівні знімки з власним
    ключем даних. Чанки лежать у сховищі за File.chunk_key(i, number) і шифруються з
    AAD = id файлу (як і поточна версія), тож перенесення версії в File не потребує
    перешифрування. Розмір кожної версії враховується в квоті власника."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name="versions")
    # Номер версії вмісту (= File.content_version, яку вона мала, коли була поточною)
    number = models.PositiveIntegerField()
    size = models.BigIntegerField()
    mime_type = models.CharField(max_length=127, default="application/octet-stream")
    sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=12, choices=File.Status.choices, default=File.Status.UPLOADING, db_index=True)
    scan_detail = models.CharField(max_length=200, blank=True)
    wrapped_key = models.BinaryField()
    key_version = models.PositiveSmallIntegerField(default=1)
    chunk_size = models.PositiveIntegerField()
    chunks_received = models.PositiveIntegerField(default=0)
    upload_expires_at = models.DateTimeField(null=True, blank=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # Коли цей вміст було створено (а не коли він став архівним)
    created_at = models.DateTimeField(default=timezone.now)
    archived_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(fields=["file", "number"], name="uniq_file_version_number"),
            models.CheckConstraint(condition=Q(size__gte=0), name="file_version_size_non_negative"),
        ]

    @property
    def chunks_total(self) -> int:
        if self.size == 0:
            return 0
        return (self.size + self.chunk_size - 1) // self.chunk_size

    def expected_chunk_len(self, index: int) -> int:
        if index < self.chunks_total - 1:
            return self.chunk_size
        return self.size - self.chunk_size * (self.chunks_total - 1)

    def chunk_key(self, index: int) -> str:
        return self.file.chunk_key(index, self.number)

    @property
    def is_downloadable(self) -> bool:
        if self.status == File.Status.CLEAN:
            return True
        return self.status == File.Status.UNSCANNED and settings.UNSCANNED_POLICY == "allow"


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
    """Публічне посилання: на файл, на теку (перегляд і завантаження) або «скринька для файлів»
    (drop: гості лише надсилають файли в теку, не бачачи її вмісту)."""

    class Kind(models.TextChoices):
        FILE = "file", "Файл"
        FOLDER = "folder", "Тека"
        DROP = "drop", "Скринька для файлів"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="public_links")
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.FILE)
    file = models.ForeignKey(File, null=True, blank=True, on_delete=models.CASCADE, related_name="public_links")
    folder = models.ForeignKey(Folder, null=True, blank=True, on_delete=models.CASCADE, related_name="public_links")
    # Підпис для гостей («Скиньте, будь ласка, фото з поїздки»)
    note = models.CharField(max_length=300, blank=True)
    # Скринька: скільки файлів і байтів уже надіслано та необов'язковий ліміт байтів
    upload_count = models.PositiveIntegerField(default=0)
    upload_bytes = models.BigIntegerField(default=0)
    max_upload_bytes = models.BigIntegerField(null=True, blank=True)
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

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(kind="file", file__isnull=False, folder__isnull=True) | Q(~Q(kind="file"), file__isnull=True, folder__isnull=False)),
                name="public_link_target",
            )
        ]

    @property
    def target_name(self) -> str:
        return self.file.name if self.file_id else (self.folder.name if self.folder_id else "")

    @property
    def is_active(self) -> bool:
        if self.revoked_at or self.expires_at <= timezone.now():
            return False
        if self.max_downloads is not None and self.download_count >= self.max_downloads:
            return False
        return True



# ─────────────────────────── Обране, теги, нещодавні ───────────────────────────


class Star(models.Model):
    """«Обране»: зірочка на файлі чи теці (своїй або доступній через спільний доступ)."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    file = models.ForeignKey(File, null=True, blank=True, on_delete=models.CASCADE, related_name="stars")
    folder = models.ForeignKey(Folder, null=True, blank=True, on_delete=models.CASCADE, related_name="stars")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "file"], condition=Q(file__isnull=False), name="uniq_star_file"),
            models.UniqueConstraint(fields=["owner", "folder"], condition=Q(folder__isnull=False), name="uniq_star_folder"),
            models.CheckConstraint(condition=Q(file__isnull=False) ^ Q(folder__isnull=False), name="star_one_target"),
        ]


class Tag(models.Model):
    """Особистий кольоровий тег (як мітки у Finder). Інші користувачі тегів не бачать."""

    COLORS = ["red", "orange", "yellow", "green", "teal", "blue", "violet", "pink", "gray"]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="file_tags")
    name = models.CharField(max_length=40)
    color = models.CharField(max_length=10, default="blue")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="uniq_tag_name")]


class ItemTag(models.Model):
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="items")
    file = models.ForeignKey(File, null=True, blank=True, on_delete=models.CASCADE, related_name="item_tags")
    folder = models.ForeignKey(Folder, null=True, blank=True, on_delete=models.CASCADE, related_name="item_tags")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tag", "file"], condition=Q(file__isnull=False), name="uniq_itemtag_file"),
            models.UniqueConstraint(fields=["tag", "folder"], condition=Q(folder__isnull=False), name="uniq_itemtag_folder"),
            models.CheckConstraint(condition=Q(file__isnull=False) ^ Q(folder__isnull=False), name="itemtag_one_target"),
        ]


class FileAccess(models.Model):
    """Коли користувач востаннє відкривав файл — для «Нещодавніх»."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name="+")
    accessed_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "file"], name="uniq_file_access")]
        indexes = [models.Index(fields=["user", "-accessed_at"])]
