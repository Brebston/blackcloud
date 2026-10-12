import uuid

from django.conf import settings
from django.db import models

MAX_SUBSCRIPTIONS_PER_USER = 10


class PushSubscription(models.Model):
    """Підписка браузера/пристрою на Web Push. Налаштування подій — окремо для кожного пристрою."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    # Адреса push-сервісу браузера (лише відомі сервіси, див. webpush.endpoint_allowed)
    endpoint = models.CharField(max_length=1000, unique=True)
    # Публічний ключ браузера (P-256) і секрет автентифікації — для шифрування вмісту
    p256dh = models.CharField(max_length=100)
    auth = models.CharField(max_length=40)
    user_agent = models.CharField(max_length=120, blank=True)
    notify_mail = models.BooleanField(default=True)
    notify_chat = models.BooleanField(default=True)
    notify_other = models.BooleanField(default=True)
    # Показувати текст повідомлень чату й теми листів (інакше — лише «Нове повідомлення»)
    show_previews = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Коли пристрою востаннє надсилалось сповіщення
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.user_id} {self.endpoint[:60]}"
