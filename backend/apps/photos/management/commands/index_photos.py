from django.core.management.base import BaseCommand

from apps.photos.indexing import run


class Command(BaseCommand):
    help = "Додати всі фото й відео до стрічки «Фото» і прочитати їхні метадані (EXIF) одразу, не чекаючи розкладу."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100000)

    def handle(self, *args, limit, **options):
        total = {"added": 0, "indexed": 0}
        while True:
            result = run(min(limit, 500))
            total["added"] += result["added"]
            total["indexed"] += result["indexed"]
            limit -= result["indexed"]
            self.stdout.write(f"додано {result['added']}, прочитано {result['indexed']}")
            if result["indexed"] == 0 or limit <= 0:
                break
        self.stdout.write(self.style.SUCCESS(f"Готово: додано {total['added']}, прочитано метадані {total['indexed']}"))
