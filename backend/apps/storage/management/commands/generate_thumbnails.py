from django.core.management.base import BaseCommand

from apps.storage.tasks import backfill_thumbnails


class Command(BaseCommand):
    help = "Створити мініатюри для вже завантажених зображень і PDF"

    def handle(self, *args, **options):
        count = backfill_thumbnails()
        self.stdout.write(self.style.SUCCESS(f"Створено мініатюр: {count}"))
