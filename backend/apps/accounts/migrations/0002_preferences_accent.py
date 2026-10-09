from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="preferences",
            name="accent",
            field=models.CharField(
                choices=[
                    ("violet", "Фіолетовий"),
                    ("blue", "Синій"),
                    ("teal", "Бірюзовий"),
                    ("green", "Зелений"),
                    ("amber", "Бурштиновий"),
                    ("orange", "Помаранчевий"),
                    ("rose", "Рожевий"),
                    ("slate", "Графітовий"),
                ],
                default="violet",
                max_length=12,
            ),
        ),
    ]
