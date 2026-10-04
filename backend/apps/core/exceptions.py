import logging

from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger("blackcloud")


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        # Не віддаємо клієнту трейсбеки та внутрішні деталі
        logger.exception("Unhandled API error", exc_info=exc)
        from rest_framework.response import Response

        return Response({"detail": "Внутрішня помилка сервера."}, status=500)
    return response
