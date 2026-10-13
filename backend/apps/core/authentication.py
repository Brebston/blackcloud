from rest_framework.authentication import CSRFCheck, SessionAuthentication
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import SAFE_METHODS


class CsrfSessionAuthentication(SessionAuthentication):
    """Сесійна автентифікація DRF з обов'язковою перевіркою CSRF.

    Сесія створюється лише ПІСЛЯ проходження 2FA, тому будь-який
    автентифікований запит вже пройшов усі фактори.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, auth = result
        if not user.is_active:
            return None
        return user, auth

    def authenticate_header(self, request):
        # Завдяки цьому DRF повертає 401 (а не 403) для неавтентифікованих запитів;
        # нестандартна схема не викликає діалог логіну в браузері.
        return 'Session realm="api"'


def enforce_csrf(request):
    """Перевіряє CSRF-токен так само, як Django-форми (Origin/Referer + X-CSRFToken).

    DRF перевіряє CSRF лише для вже автентифікованих сесій, тож анонімні
    запити, які створюють або змінюють сесію (вхід, 2FA, реєстрація, вихід),
    потребують явної перевірки — інакше можливий «login CSRF».
    """
    check = CSRFCheck(lambda req: None)
    check.process_request(request)
    reason = check.process_view(request, None, (), {})
    if reason:
        # Текст «CSRF Failed» SPA використовує, щоб оновити токен і повторити запит
        raise PermissionDenied(f"CSRF Failed: {reason}")


class EnforceCsrfMixin:
    """Обов'язкова CSRF-перевірка для небезпечних методів навіть без входу."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method not in SAFE_METHODS:
            enforce_csrf(request)
