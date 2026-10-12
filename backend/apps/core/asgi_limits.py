"""ASGI-обгортка, що обмежує тіло запиту ДО того, як Django почне його читати.

Django ASGIHandler спершу повністю вичитує тіло (у пам'ять і /tmp) і лише потім
викликає middleware. Тому перевірка лише в Django-middleware не захищає від
запитів з `Transfer-Encoding: chunked` без Content-Length: такий запит міг би
заповнити /tmp контейнера. Тут:
  * запит з chunked-тілом без Content-Length → 411 Length Required;
  * Content-Length більше ліміту → 413;
  * якщо клієнт надсилає більше байтів, ніж оголосив, — з'єднання обривається.
"""

import json

from django.utils import translation
from django.utils.translation import gettext_noop

from .i18n import language_from_header


async def _reply(send, status: int, detail: str, headers: dict):
    with translation.override(language_from_header(headers.get(b"accept-language"))):
        detail = translation.gettext(detail)
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


def is_dav_path(path: str) -> bool:
    """WebDAV (/dav/): Finder надсилає PUT з chunked-тілом без Content-Length (з
    X-Expected-Entity-Length). Лише тут chunked дозволено, а ліміт — MAX_FILE_SIZE."""
    return path == "/dav" or path.startswith("/dav/")


def dav_max_body() -> int:
    from django.conf import settings

    return settings.MAX_FILE_SIZE


class BodyLimitMiddleware:
    def __init__(self, app, max_body: int):
        self.app = app
        self.max_body = max_body

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {}
        for name, value in scope.get("headers", []):
            headers[name.lower()] = value
        length_raw = headers.get(b"content-length")
        chunked = b"chunked" in headers.get(b"transfer-encoding", b"").lower()
        dav = is_dav_path(scope.get("path", ""))
        max_body = dav_max_body() if dav else self.max_body

        if length_raw is None:
            if chunked and not dav:
                return await _reply(send, 411, gettext_noop("Потрібен заголовок Content-Length."), headers)
            declared = 0
            if chunked:
                expected = headers.get(b"x-expected-entity-length")
                try:
                    if expected is not None and int(expected) > max_body:
                        return await _reply(send, 413, gettext_noop("Запит завеликий."), headers)
                except ValueError:
                    return await _reply(send, 400, gettext_noop("Невірний Content-Length."), headers)
                # Тіло невідомої довжини: обриваємо, щойно воно перевищить ліміт
                declared = max_body
        else:
            try:
                declared = int(length_raw)
            except ValueError:
                return await _reply(send, 400, gettext_noop("Невірний Content-Length."), headers)
            if declared < 0:
                return await _reply(send, 400, gettext_noop("Невірний Content-Length."), headers)
            if declared > max_body:
                return await _reply(send, 413, gettext_noop("Запит завеликий."), headers)

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > declared:
                    # Більше байтів, ніж оголошено, — обриваємо обробку
                    return {"type": "http.disconnect"}
            return message

        return await self.app(scope, limited_receive, send)
