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



async def _reply(send, status: int, detail: str):
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


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

        if length_raw is None:
            if chunked:
                return await _reply(send, 411, "Потрібен заголовок Content-Length.")
            declared = 0
        else:
            try:
                declared = int(length_raw)
            except ValueError:
                return await _reply(send, 400, "Невірний Content-Length.")
            if declared < 0:
                return await _reply(send, 400, "Невірний Content-Length.")
            if declared > self.max_body:
                return await _reply(send, 413, "Запит завеликий.")

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
