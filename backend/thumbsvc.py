"""Ізольований HTTP-сервіс мініатюр (див. apps/storage/thumbrender.py).

POST /render?kind=image|pdf  (тіло — відкриті байти файлу)  →  200 image/webp | 422
Працює без Django, без секретів і в окремій внутрішній мережі `thumbs`,
до якої підключений лише worker.
"""

import logging
import os
import resource
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from apps.storage import thumbrender

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s thumbsvc %(message)s")
log = logging.getLogger("thumbsvc")
SLOTS = threading.BoundedSemaphore(int(os.environ.get("THUMB_CONCURRENCY", "2")))


class Handler(BaseHTTPRequestHandler):
    server_version = "thumbsvc"
    sys_version = ""

    def log_message(self, fmt, *args):  # без шляхів і IP у логах
        return

    def _reply(self, code: int, body: bytes = b"", ctype: str = "text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._reply(200, b"ok")
        return self._reply(404)

    def do_POST(self):
        url = urlsplit(self.path)
        if url.path != "/render":
            return self._reply(404)
        kind = (parse_qs(url.query).get("kind") or [""])[0]
        try:
            length = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            length = -1
        if length < 0:
            return self._reply(411)
        if length > thumbrender.MAX_INPUT_BYTES:
            return self._reply(413)
        data = self.rfile.read(length)
        if not SLOTS.acquire(timeout=60):
            return self._reply(503)
        try:
            out = thumbrender.render(kind, data)
        except Exception as exc:
            log.info("render failed: %s", exc.__class__.__name__)
            return self._reply(422)
        finally:
            SLOTS.release()
        return self._reply(200, out, "image/webp")


def main():
    # Жорсткі ліміти процесу: пам'ять і час CPU на дочірні pdftoppm теж поширюються
    mem = int(os.environ.get("THUMB_MAX_MEMORY_MB", "700")) * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    except (ValueError, OSError):
        pass
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("THUMB_PORT", "8100"))), Handler)
    server.daemon_threads = True
    log.info("listening")
    server.serve_forever()


if __name__ == "__main__":
    main()
