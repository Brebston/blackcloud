from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.audit import audit
from apps.core.realtime import notify

from . import ics
from .models import Calendar, CalendarShare, Event
from .recurrence import occurrences
from .serializers import CalendarSerializer, CalendarShareSerializer, EventSerializer

User = get_user_model()
MAX_RANGE = timedelta(days=400)
MAX_ICS_UPLOAD = 5 * 1024 * 1024


def visible_calendars(user):
    return Calendar.objects.filter(Q(owner=user) | Q(shares__user=user)).distinct()


def editable_calendar(user, calendar_id) -> Calendar:
    cal = Calendar.objects.filter(pk=calendar_id).filter(Q(owner=user) | Q(shares__user=user, shares__can_edit=True)).first()
    if cal is None:
        raise PermissionDenied("Немає прав на редагування цього календаря.")
    return cal


class CalendarViewSet(viewsets.ModelViewSet):
    serializer_class = CalendarSerializer
    pagination_class = None

    def get_queryset(self):
        return visible_calendars(self.request.user).select_related("owner")

    def perform_create(self, serializer):
        if Calendar.objects.filter(owner=self.request.user).count() >= 50:
            raise ValidationError({"detail": "Забагато календарів."})
        serializer.save(owner=self.request.user)

    def _own(self):
        cal = self.get_object()
        if cal.owner_id != self.request.user.pk:
            raise PermissionDenied("Лише власник може це зробити.")
        return cal

    def perform_update(self, serializer):
        self._own()
        serializer.save()

    def perform_destroy(self, instance):
        self._own()
        if Calendar.objects.filter(owner=self.request.user).count() <= 1:
            raise ValidationError({"detail": "Не можна видалити останній календар."})
        audit(self.request, "calendar.deleted", target=str(instance.pk))
        instance.delete()

    @action(detail=True, methods=["post"])
    def share(self, request, pk=None):
        cal = self._own()
        ser = CalendarShareSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = User.objects.filter(username=ser.validated_data["username"].lower(), is_active=True).first()
        if user is None or user == request.user:
            raise ValidationError({"username": ["Користувача не знайдено."]})
        CalendarShare.objects.update_or_create(
            calendar=cal, user=user, defaults={"can_edit": ser.validated_data["can_edit"]}
        )
        notify(user, "calendar", f"{request.user.username} поділився календарем", cal.name, "/calendar")
        audit(request, "calendar.shared", target=str(cal.pk), recipient=user.username)
        return Response(CalendarSerializer(cal, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def unshare(self, request, pk=None):
        cal = self.get_object()
        username = (request.data.get("username") or "").lower()
        if cal.owner_id == request.user.pk:
            CalendarShare.objects.filter(calendar=cal, user__username=username).delete()
        else:
            # одержувач може відписатися сам
            CalendarShare.objects.filter(calendar=cal, user=request.user).delete()
        return Response({"status": "ok"})

    @action(detail=True, methods=["post"])
    def feed(self, request, pk=None):
        """Створює/перевипускає приватний URL підписки ICS (лише для читання)."""
        cal = self._own()
        if request.data.get("disable"):
            cal.feed_token_hash = ""
            cal.save(update_fields=["feed_token_hash"])
            return Response({"url": None})
        token = cal.rotate_feed_token()
        audit(request, "calendar.feed_rotated", target=str(cal.pk))
        from django.conf import settings

        return Response({"url": f"https://{settings.DOMAIN}/api/public/calendar/{token}.ics"})

    @action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        cal = self.get_object()
        data = ics.export_calendar(cal, cal.events.all())
        response = HttpResponse(data, content_type="text/calendar; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="calendar.ics"'
        return response

    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser], url_path="import")
    def import_ics(self, request, pk=None):
        cal = editable_calendar(request.user, pk)
        upload = request.FILES.get("file")
        if upload is None or upload.size > MAX_ICS_UPLOAD:
            raise ValidationError({"file": ["Потрібен .ics файл до 5 МБ."]})
        try:
            count = ics.import_ics(cal, upload.read(), request.user)
        except Exception:
            raise ValidationError({"file": ["Не вдалося прочитати ICS."]})
        audit(request, "calendar.imported", target=str(cal.pk), count=count)
        return Response({"imported": count})


class EventViewSet(viewsets.ModelViewSet):
    serializer_class = EventSerializer
    pagination_class = None

    def get_queryset(self):
        return Event.objects.filter(calendar__in=visible_calendars(self.request.user))

    def list(self, request):
        start = parse_datetime(request.query_params.get("start") or "")
        end = parse_datetime(request.query_params.get("end") or "")
        if not start or not end or end <= start or end - start > MAX_RANGE:
            raise ValidationError({"detail": "Вкажіть start і end (ISO 8601), діапазон до 400 днів."})
        if timezone.is_naive(start):
            start = timezone.make_aware(start)
        if timezone.is_naive(end):
            end = timezone.make_aware(end)
        qs = self.get_queryset().filter(Q(rrule="", start__lt=end, end__gt=start) | ~Q(rrule="")).filter(
            start__lt=end
        )
        cal_filter = request.query_params.getlist("calendar")
        if cal_filter:
            qs = qs.filter(calendar_id__in=cal_filter)
        result = []
        for ev in qs.select_related("calendar"):
            base = EventSerializer(ev).data
            base["color"] = ev.calendar.color
            for occ_start, occ_end in occurrences(ev, start, end):
                item = dict(base)
                item["occurrence_start"] = occ_start
                item["occurrence_end"] = occ_end
                result.append(item)
        result.sort(key=lambda x: x["occurrence_start"])
        return Response(result)

    def perform_create(self, serializer):
        cal = editable_calendar(self.request.user, serializer.validated_data["calendar"].pk)
        serializer.save(calendar=cal, created_by=self.request.user)

    def perform_update(self, serializer):
        editable_calendar(self.request.user, serializer.instance.calendar_id)
        if "calendar" in serializer.validated_data:
            editable_calendar(self.request.user, serializer.validated_data["calendar"].pk)
        serializer.save(last_reminded_for=None)

    def perform_destroy(self, instance):
        editable_calendar(self.request.user, instance.calendar_id)
        instance.delete()


class PublicFeedView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_link"

    def get(self, request, token):
        import hashlib

        if not token:
            raise NotFound()
        cal = get_object_or_404(Calendar, feed_token_hash=hashlib.sha256(token.encode()).hexdigest())
        data = ics.export_calendar(cal, cal.events.all())
        response = HttpResponse(data, content_type="text/calendar; charset=utf-8")
        response["Cache-Control"] = "private, max-age=300"
        return response
