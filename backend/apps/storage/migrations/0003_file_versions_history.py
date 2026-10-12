import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0002_file_versions_thumbnails"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="file",
            name="modified_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="file",
            name="content_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="file",
            name="version_counter",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name="FileVersion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("number", models.PositiveIntegerField()),
                ("size", models.BigIntegerField()),
                ("mime_type", models.CharField(default="application/octet-stream", max_length=127)),
                ("sha256", models.CharField(blank=True, max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("uploading", "Завантажується"),
                            ("scanning", "Перевірка антивірусом"),
                            ("clean", "Готовий"),
                            ("infected", "Заражений (карантин)"),
                            ("unscanned", "Не перевірено (завеликий)"),
                            ("failed", "Помилка"),
                        ],
                        db_index=True,
                        default="uploading",
                        max_length=12,
                    ),
                ),
                ("scan_detail", models.CharField(blank=True, max_length=200)),
                ("wrapped_key", models.BinaryField()),
                ("key_version", models.PositiveSmallIntegerField(default=1)),
                ("chunk_size", models.PositiveIntegerField()),
                ("chunks_received", models.PositiveIntegerField(default=0)),
                ("upload_expires_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("archived_at", models.DateTimeField(auto_now_add=True)),
                (
                    "author",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "file",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="versions", to="storage.file"
                    ),
                ),
            ],
            options={
                "ordering": ["-number"],
                "constraints": [
                    models.UniqueConstraint(fields=("file", "number"), name="uniq_file_version_number"),
                    models.CheckConstraint(condition=models.Q(("size__gte", 0)), name="file_version_size_non_negative"),
                ],
            },
        ),
    ]
