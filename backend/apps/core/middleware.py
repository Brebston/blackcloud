from django.utils.translation import gettext_noop


class NoCacheApiMiddleware:
    """Відповіді API не кешуються браузером і проксі."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/") and "Cache-Control" not in response:
            response["Cache-Control"] = "no-store"
            response["Pragma"] = "no-cache"
        return response


class MaxBodySizeMiddleware:
    """Відхиляє запити з тілом більше MAX_REQUEST_BODY (захист від заповнення диска)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        from django.http import JsonResponse
        from django.utils import translation
        from django.utils.translation import gettext as _

        def reject(detail, status):
            # Працює до LocaleMiddleware, тому мову з Accept-Language визначаємо тут
            with translation.override(translation.get_language_from_request(request)):
                return JsonResponse({"detail": _(detail)}, status=status)

        if request.META.get("CONTENT_LENGTH") in (None, "") and "chunked" in request.META.get(
            "HTTP_TRANSFER_ENCODING", ""
        ).lower():
            return reject(gettext_noop("Потрібен заголовок Content-Length."), 411)
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            return reject(gettext_noop("Невірний Content-Length."), 400)
        if length > settings.MAX_REQUEST_BODY:
            return reject(gettext_noop("Запит завеликий."), 413)
        return self.get_response(request)
