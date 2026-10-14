from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0003_file_versions_history"),
    ]

    operations = [
        migrations.AddField(
            model_name="file",
            name="has_preview",
            field=models.BooleanField(default=False),
        ),
    ]
