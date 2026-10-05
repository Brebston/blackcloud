import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from django.conf import settings  # noqa: E402

from apps.chat.routing import websocket_urlpatterns  # noqa: E402
from apps.core.asgi_limits import BodyLimitMiddleware  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": BodyLimitMiddleware(django_asgi_app, settings.MAX_REQUEST_BODY),
        # Перевірка Origin + автентифікація через ту саму сесійну cookie
        "websocket": AllowedHostsOriginValidator(AuthMiddlewareStack(URLRouter(websocket_urlpatterns))),
    }
)
