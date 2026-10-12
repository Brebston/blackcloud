import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PushSubscription",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("endpoint", models.CharField(max_length=1000, unique=True)),
                ("p256dh", models.CharField(max_length=100)),
                ("auth", models.CharField(max_length=40)),
                ("user_agent", models.CharField(blank=True, max_length=120)),
                ("notify_mail", models.BooleanField(default=True)),
                ("notify_chat", models.BooleanField(default=True)),
                ("notify_other", models.BooleanField(default=True)),
                ("show_previews", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="push_subscriptions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["created_at"]},
        ),
    ]
