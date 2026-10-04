from django.apps import AppConfig


class MailConfig(AppConfig):
    name = "apps.mail"
    label = "mail"
    verbose_name = "Пошта"

    def ready(self):
        from django.db.models.signals import post_migrate

        from . import signals  # noqa: F401
        from .sql import create_mail_views

        post_migrate.connect(create_mail_views, sender=self, dispatch_uid="bc_mail_views")
