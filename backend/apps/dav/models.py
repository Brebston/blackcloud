import uuid

from django.conf import settings
from django.db import models


class DavPassword(models.Model):
    """Пароль застосунку для WebDAV (Basic auth не підтримує 2FA).

    Генерується сервером, показується один раз; у БД лише хеш (Argon2 у продакшні).
    Основний пароль акаунта для WebDAV не приймається."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="dav_passwords")
    label = models.CharField(max_length=60)
    password_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "created_at"])]

    def __str__(self):
        return f"{self.user} / {self.label}"
