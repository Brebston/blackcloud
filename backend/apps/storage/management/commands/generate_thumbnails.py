from django.core.management.base import BaseCommand

from apps.storage.tasks import backfill_thumbnails


class Command(BaseCommand):
    help = "Створити мініатюри (зображення, PDF, відео) і великі зображення для HEIC/TIFF, яких бракує"

    def add_arguments(self, parser):
        parser.add_argument("--retry", action="store_true", help="повторити й ті, що вже не вдалися 3 рази")
        parser.add_argument("--limit", type=int, default=5000)

    def handle(self, *args, **options):
        count = backfill_thumbnails(options["limit"], retry_failed=options["retry"])
        self.stdout.write(self.style.SUCCESS(f"Створено мініатюр: {count}"))
