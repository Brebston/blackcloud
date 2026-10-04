from django.core.cache import cache
from django.utils import timezone

from .models import UserSession


class SessionTrackingMiddleware:
    """Оновлює last_seen сесії не частіше ніж раз на 5 хвилин."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            key = request.session.session_key
            if key and cache.add(f"sess-seen:{key}", 1, 300):
                UserSession.objects.filter(session_key=key).update(last_seen=timezone.now())
        return response
