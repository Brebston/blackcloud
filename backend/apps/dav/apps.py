import logging
import os
import tempfile

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger("blackcloud")


class DavConfig(AppConfig):
    name = "apps.dav"
    label = "dav"
    verbose_name = "WebDAV"

    def ready(self):
        # Django (ASGI) спершу повністю зберігає тіло запиту в SpooledTemporaryFile у
        # tempfile.gettempdir(). У контейнері /tmp — tmpfs на 256 МБ, тож великі PUT
        # через WebDAV туди не помістяться. Тому тимчасові файли пишемо на том
        # з об'єктами (FILE_UPLOAD_TEMP_DIR), якщо його задано.
        spool = getattr(settings, "FILE_UPLOAD_TEMP_DIR", None)
        if not spool:
            return
        try:
            os.makedirs(spool, mode=0o700, exist_ok=True)
            if not os.access(spool, os.W_OK):
                raise OSError("not writable")
            tempfile.tempdir = str(spool)
        except OSError:
            logger.warning("upload spool dir %s is not usable, using %s", spool, tempfile.gettempdir())
            # Інакше Django не зміг би зберегти й звичайні multipart-завантаження
            settings.FILE_UPLOAD_TEMP_DIR = None
