import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def fill_maildir(apps, schema_editor):
    """Наявні скриньки вже лежать на диску в каталозі <local_part> — так і лишаємо."""
    Mailbox = apps.get_model("mail", "Mailbox")
    for mb in Mailbox.objects.all().only("id", "local_part"):
        Mailbox.objects.filter(pk=mb.pk).update(maildir=mb.local_part.lower())


class Migration(migrations.Migration):
    dependencies = [
        ("mail", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="mailbox",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="mailboxes", to=settings.AUTH_USER_MODEL
            ),
        ),
        migrations.AddField(
            model_name="mailbox",
            name="display_name",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="mailbox",
            name="maildir",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.RunPython(fill_maildir, migrations.RunPython.noop),
        migrations.AlterModelOptions(name="mailbox", options={"ordering": ["created_at"]}),
        migrations.AddConstraint(
            model_name="mailbox",
            constraint=models.UniqueConstraint(fields=("domain", "maildir"), name="uniq_mailbox_maildir"),
        ),
        migrations.CreateModel(
            name="Signature",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=60)),
                ("html", models.TextField(max_length=200000)),
                ("is_default", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mail_signatures",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="ConfidentialMessage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("from_address", models.CharField(max_length=320)),
                ("recipients", models.TextField(blank=True)),
                ("subject", models.CharField(blank=True, max_length=300)),
                ("html_encrypted", models.TextField(blank=True)),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("passcode_hash", models.CharField(blank=True, max_length=200)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("view_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "sender",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="confidential_messages",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
