from datetime import datetime

from dateutil.rrule import rrulestr
from rest_framework.exceptions import ValidationError

MAX_OCCURRENCES = 500
ALLOWED_KEYS = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "BYMONTH", "BYSETPOS", "WKST"}


def validate_rrule(rule: str, dtstart: datetime) -> str:
    rule = (rule or "").strip().upper().removeprefix("RRULE:")
    if not rule:
        return ""
    keys = {part.split("=", 1)[0] for part in rule.split(";") if part}
    if not keys <= ALLOWED_KEYS or "FREQ" not in keys:
        raise ValidationError({"rrule": ["Непідтримуване правило повторення."]})
    if "FREQ=SECONDLY" in rule or "FREQ=MINUTELY" in rule or "FREQ=HOURLY" in rule:
        raise ValidationError({"rrule": ["Надто часте повторення."]})
    try:
        rrulestr(rule, dtstart=dtstart)
    except (ValueError, TypeError) as exc:
        raise ValidationError({"rrule": [f"Невірне правило: {exc}"]})
    return rule


def occurrences(event, range_start: datetime, range_end: datetime):
    """Повертає список (start, end) входжень події у заданому діапазоні."""
    duration = event.end - event.start
    if not event.rrule:
        if event.start < range_end and event.end > range_start:
            return [(event.start, event.end)]
        return []
    rule = rrulestr(event.rrule, dtstart=event.start)
    result = []
    for start in rule.xafter(range_start - duration, inc=True):
        if start >= range_end or len(result) >= MAX_OCCURRENCES:
            break
        result.append((start, start + duration))
    return result
