from rest_framework.authentication import SessionAuthentication


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
