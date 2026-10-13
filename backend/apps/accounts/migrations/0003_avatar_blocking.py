import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_preferences_accent")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="avatar_version",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="user",
            name="blocked_reason",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="user",
            name="blocked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="blocked_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="UserAvatar",
            fields=[
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="avatar",
                        serialize=False,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("image", models.BinaryField(blank=True, default=b"")),
                ("content_type", models.CharField(blank=True, max_length=40)),
                ("pending", models.BinaryField(blank=True, default=b"")),
                ("pending_id", models.UUIDField(blank=True, null=True)),
                ("failed", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
