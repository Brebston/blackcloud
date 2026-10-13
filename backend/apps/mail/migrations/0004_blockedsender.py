import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mail", "0003_scheduledmessage"),
    ]

    operations = [
        migrations.CreateModel(
            name="BlockedSender",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("address", models.CharField(max_length=320)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "mailbox",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="blocked_senders", to="mail.mailbox"
                    ),
                ),
            ],
            options={
                "ordering": ["address"],
                "constraints": [models.UniqueConstraint(fields=("mailbox", "address"), name="uniq_blocked_sender")],
            },
        ),
    ]
