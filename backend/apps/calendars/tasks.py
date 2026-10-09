from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_noop

from apps.core.realtime import notify

from .models import Event
from .recurrence import occurrences

LOOKAHEAD = timedelta(days=30)


@shared_task
def send_due_reminders():
    """Раз на хвилину: надсилає нагадування про події, час яких настав."""
    now = timezone.now()
    candidates = Event.objects.filter(reminder_minutes__isnull=False).filter(
        Q(rrule="", start__gt=now, start__lte=now + LOOKAHEAD) | ~Q(rrule="")
    ).select_related("calendar__owner")
    for ev in candidates.iterator():
        delta = timedelta(minutes=ev.reminder_minutes)
        for occ_start, _ in occurrences(ev, now, now + delta + timedelta(minutes=1))[:1]:
            if occ_start - delta > now or occ_start < now:
                continue
            if ev.last_reminded_for and ev.last_reminded_for >= occ_start:
                continue
            Event.objects.filter(pk=ev.pk).update(last_reminded_for=occ_start)
            when = timezone.localtime(occ_start).strftime("%d.%m %H:%M")
            recipients = {ev.calendar.owner} | {s.user for s in ev.calendar.shares.select_related("user")}
            for user in recipients:
                notify(
                    user,
                    "reminder",
                    gettext_noop("Нагадування: %(title)s"),
                    gettext_noop("Початок о %(when)s"),
                    "/calendar",
                    params={"title": ev.title, "when": when},
                )
