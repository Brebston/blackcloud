import hashlib
import secrets
import uuid

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q

COLOR_VALIDATOR = RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Колір у форматі #RRGGBB")


class Calendar(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendars")
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, default="#7F77DD", validators=[COLOR_VALIDATOR])
    created_at = models.DateTimeField(auto_now_add=True)
    # Приватна ICS-підписка (зберігаємо лише хеш токена)
    feed_token_hash = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.name

    def rotate_feed_token(self) -> str:
        token = secrets.token_urlsafe(32)
        self.feed_token_hash = hashlib.sha256(token.encode()).hexdigest()
        self.save(update_fields=["feed_token_hash"])
        return token


class CalendarShare(models.Model):
    calendar = models.ForeignKey(Calendar, on_delete=models.CASCADE, related_name="shares")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shared_calendars")
    can_edit = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["calendar", "user"], name="uniq_calendar_share")]


class Event(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    uid = models.CharField(max_length=255, blank=True, help_text="UID з iCalendar")
    calendar = models.ForeignKey(Calendar, on_delete=models.CASCADE, related_name="events")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, max_length=10000)
    location = models.CharField(max_length=300, blank=True)
    start = models.DateTimeField(db_index=True)
    end = models.DateTimeField()
    all_day = models.BooleanField(default=False)
    # Правило повторення RFC 5545, напр. "FREQ=WEEKLY;BYDAY=MO,WE"
    rrule = models.CharField(max_length=300, blank=True)
    reminder_minutes = models.PositiveIntegerField(null=True, blank=True)
    last_reminded_for = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start"]
        constraints = [models.CheckConstraint(condition=Q(end__gte=F("start")), name="event_end_after_start")]

    def save(self, *args, **kwargs):
        if not self.uid:
            self.uid = f"{self.id}@blackcloud"
        super().save(*args, **kwargs)
