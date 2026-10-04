from datetime import date, datetime, time, timedelta

from django.utils import timezone
from icalendar import Calendar as ICal
from icalendar import Event as IEvent
from icalendar import vRecur

from .models import Event

MAX_IMPORT_EVENTS = 5000


def export_calendar(calendar, events) -> bytes:
    cal = ICal()
    cal.add("prodid", "-//BlackCloud//Calendar//UK")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", calendar.name)
    for ev in events:
        ie = IEvent()
        ie.add("uid", ev.uid)
        ie.add("summary", ev.title)
        if ev.description:
            ie.add("description", ev.description)
        if ev.location:
            ie.add("location", ev.location)
        if ev.all_day:
            ie.add("dtstart", timezone.localtime(ev.start).date())
            ie.add("dtend", timezone.localtime(ev.end).date() + timedelta(days=1))
        else:
            ie.add("dtstart", ev.start)
            ie.add("dtend", ev.end)
        if ev.rrule:
            ie.add("rrule", vRecur.from_ical(ev.rrule))
        ie.add("dtstamp", ev.updated_at)
        cal.add_component(ie)
    return cal.to_ical()


def _to_dt(value, end=False):
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            value = timezone.make_aware(value)
        return value, False
    if isinstance(value, date):
        dt = datetime.combine(value, time.min)
        if end:
            dt -= timedelta(seconds=1)
        return timezone.make_aware(dt), True
    raise ValueError("bad date")


def import_ics(calendar, data: bytes, user) -> int:
    from .recurrence import validate_rrule

    cal = ICal.from_ical(data)
    count = 0
    for comp in cal.walk("VEVENT"):
        if count >= MAX_IMPORT_EVENTS:
            break
        try:
            start, all_day = _to_dt(comp.decoded("dtstart"))
            if comp.get("dtend") is not None:
                end, _ = _to_dt(comp.decoded("dtend"), end=True)
            else:
                end = start + (timedelta(days=1) - timedelta(seconds=1) if all_day else timedelta(hours=1))
            if end < start:
                end = start
            rrule = ""
            if comp.get("rrule") is not None:
                try:
                    rrule = validate_rrule(comp.get("rrule").to_ical().decode(), start)
                except Exception:
                    rrule = ""
            uid = str(comp.get("uid", ""))[:255]
            defaults = {
                "title": str(comp.get("summary", "Без назви"))[:200],
                "description": str(comp.get("description", ""))[:10000],
                "location": str(comp.get("location", ""))[:300],
                "start": start,
                "end": end,
                "all_day": all_day,
                "rrule": rrule,
                "created_by": user,
            }
            if uid:
                Event.objects.update_or_create(calendar=calendar, uid=uid, defaults=defaults)
            else:
                Event.objects.create(calendar=calendar, **defaults)
            count += 1
        except Exception:
            continue
    return count
