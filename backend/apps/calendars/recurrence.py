"""Повторювані події (RRULE) із захистом від «важких» правил.

Правило на кшталт «31 лютого щороку» не має жодного входження, і dateutil
перебирав би роки аж до 9999 — секунди CPU на кожен запит календаря. Тому:
  * при збереженні правило перевіряється «пробою» з межею UNTIL = старт + 8 років:
    якщо за цей час немає жодного входження — правило відхиляється;
  * при розгортанні ітерація завжди обмежена кінцем запитаного діапазону;
  * дата початку повторюваної події — не раніше 1970 року, діапазон запиту — до 2200.
"""

from datetime import datetime, timedelta, timezone as dt_timezone

from dateutil.rrule import rrulestr
from django.conf import settings
from rest_framework.exceptions import ValidationError

MAX_OCCURRENCES = 500
ALLOWED_KEYS = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "BYMONTH", "BYSETPOS", "WKST"}
PROBE_SPAN = timedelta(days=366 * 8)
MIN_YEAR = 1970
MAX_YEAR = 2200
MAX_INTERVAL = 1000


def _bad(msg: str):
    raise ValidationError({"rrule": [msg]})


def validate_rrule(rule: str, dtstart: datetime) -> str:
    rule = (rule or "").strip().upper().removeprefix("RRULE:")
    if not rule:
        return ""
    if len(rule) > 300:
        _bad("Задовге правило повторення.")
    parts = dict(part.split("=", 1) for part in rule.split(";") if "=" in part)
    keys = {part.split("=", 1)[0] for part in rule.split(";") if part}
    if not keys <= ALLOWED_KEYS or "FREQ" not in keys:
        _bad("Непідтримуване правило повторення.")
    if parts.get("FREQ") in {"SECONDLY", "MINUTELY", "HOURLY"}:
        _bad("Надто часте повторення.")
    if "INTERVAL" in parts and (not parts["INTERVAL"].isdigit() or not 1 <= int(parts["INTERVAL"]) <= MAX_INTERVAL):
        _bad("Невірний інтервал повторення.")
    if dtstart is None or not MIN_YEAR <= dtstart.year <= MAX_YEAR:
        _bad(f"Повторювані події можуть починатися з {MIN_YEAR} до {MAX_YEAR} року.")
    try:
        parsed = rrulestr(rule, dtstart=dtstart)
    except (ValueError, TypeError) as exc:
        _bad(f"Невірне правило: {exc}")
    # Проба: перше входження має бути протягом 8 років від початку
    try:
        cap = dtstart + PROBE_SPAN
        until = parsed._until
        if until is not None and until.tzinfo is None and cap.tzinfo is not None:
            until = until.replace(tzinfo=dt_timezone.utc)
        probe = parsed.replace(count=None, until=min(until, cap) if until else cap)
        first = probe.after(dtstart - timedelta(seconds=1), inc=True)
    except (ValueError, TypeError, OverflowError) as exc:
        _bad(f"Невірне правило: {exc}")
    if first is None:
        _bad("За правилом подія не повторюється жодного разу (перевірте дати).")
    return rule


def recurring_quota_left(user) -> int:
    from .models import Event

    used = Event.objects.filter(created_by=user).exclude(rrule="").count()
    return max(settings.MAX_RECURRING_EVENTS_PER_USER - used, 0)


def occurrences(event, range_start: datetime, range_end: datetime):
    """Повертає список (start, end) входжень події у заданому діапазоні."""
    duration = event.end - event.start
    if not event.rrule:
        if event.start < range_end and event.end > range_start:
            return [(event.start, event.end)]
        return []
    if event.start.year < MIN_YEAR or range_end.year > MAX_YEAR:
        return []
    try:
        rule = rrulestr(event.rrule, dtstart=event.start)
        until = rule._until
        if until is not None and until.tzinfo is None:
            until = until.replace(tzinfo=dt_timezone.utc)
        # Ітерація ніколи не виходить за кінець діапазону (навіть для «порожніх» правил)
        if rule._count is None:
            rule = rule.replace(until=min(until, range_end) if until else range_end)
    except (ValueError, TypeError, OverflowError):
        return []
    result = []
    for start in rule.xafter(range_start - duration, inc=True):
        if start >= range_end or len(result) >= MAX_OCCURRENCES:
            break
        result.append((start, start + duration))
    return result
