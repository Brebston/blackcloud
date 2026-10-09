"""Мова поза HTTP-запитом (сповіщення, Celery, WebSocket): береться з налаштувань користувача."""

from contextlib import contextmanager

from django.conf import settings
from django.utils import translation

SUPPORTED = {code for code, _ in settings.LANGUAGES}


def user_language(user) -> str:
    try:
        lang = user.preferences.language
    except Exception:  # немає налаштувань (новий/системний користувач)
        lang = None
    return lang if lang in SUPPORTED else settings.LANGUAGE_CODE


@contextmanager
def language_of(user):
    with translation.override(user_language(user)):
        yield


def language_from_header(accept_language: str | bytes | None) -> str:
    """Мова з Accept-Language поза Django-запитом (ASGI-обгортки до LocaleMiddleware)."""
    from django.utils.translation import get_supported_language_variant
    from django.utils.translation.trans_real import parse_accept_lang_header

    if isinstance(accept_language, bytes):
        accept_language = accept_language.decode("latin-1")
    for code, _q in parse_accept_lang_header(accept_language or ""):
        if code == "*":
            break
        try:
            return get_supported_language_variant(code)
        except LookupError:
            continue
    return settings.LANGUAGE_CODE


def render(template: str, params: dict | None = None) -> str:
    """Перекладає шаблон (позначений gettext_noop) у поточній мові й підставляє %(name)s."""
    from django.utils.translation import gettext

    if not template:
        return ""
    text = gettext(template)
    return text % params if params else text
