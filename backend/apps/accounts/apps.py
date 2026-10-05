from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Облікові записи"

    def ready(self):
        from django.core import checks

        from . import signals  # noqa: F401

        checks.register(check_registration, checks.Tags.security, deploy=True)


def check_registration(app_configs=None, **kwargs):
    from django.conf import settings
    from django.core.checks import Warning

    if settings.REGISTRATION_OPEN and not settings.IS_LOCAL_DOMAIN:
        return [
            Warning(
                "REGISTRATION_OPEN=true на публічному домені: будь-хто може створити акаунт "
                "і отримати поштову скриньку на вашому домені.",
                hint="Поставте REGISTRATION_OPEN=false і видавайте запрошення.",
                id="blackcloud.W001",
            )
        ]
    return []
