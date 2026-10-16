"""Галерея «Фото»: метадані знімків і відео (дата зйомки, камера, координати) та альбоми.

Самі файли лишаються там, де лежали (у теках «Файлів»); тут — лише індекс для стрічки
й альбоми-посилання. Координати в БД зашифровані (Fernet, як інші чутливі поля).
"""

import uuid

from django.conf import settings
from django.db import models


class PhotoMeta(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Очікує обробки"
        OK = "ok", "Готово"
        FAILED = "failed", "Не вдалося прочитати"

    file = models.OneToOneField("storage.File", on_delete=models.CASCADE, primary_key=True, related_name="photo")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=8, default="photo")  # photo | video
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING, db_index=True)
    # Для якої версії вмісту прочитано (нова версія файлу → прочитати знову)
    content_version = models.PositiveIntegerField(default=0)
    attempts = models.PositiveSmallIntegerField(default=0)
    taken_at = models.DateTimeField(null=True, blank=True)
    # exif | video | name | upload — звідки взято дату
    taken_source = models.CharField(max_length=8, default="upload")
    # Дата для стрічки: дата зйомки, а якщо її немає — дата завантаження
    sort_at = models.DateTimeField()
    camera_make = models.CharField(max_length=100, blank=True)
    camera_model = models.CharField(max_length=100, blank=True)
    lens = models.CharField(max_length=100, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    duration = models.FloatField(null=True, blank=True)
    # Fernet("lat,lon[,alt]") — розшифровується лише для власника у панелі «Інформація»
    location = models.TextField(blank=True)
    has_location = models.BooleanField(default=False)
    extracted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["owner", "-sort_at"], name="photo_timeline")]


class Album(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="photo_albums")
    name = models.CharField(max_length=100)
    cover = models.ForeignKey("storage.File", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class AlbumItem(models.Model):
    """Посилання на файл (не копія): видалили файл — він зникає й з альбому."""

    album = models.ForeignKey(Album, on_delete=models.CASCADE, related_name="items")
    file = models.ForeignKey("storage.File", on_delete=models.CASCADE, related_name="album_items")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["album", "file"], name="uniq_album_file")]
