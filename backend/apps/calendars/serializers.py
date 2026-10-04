from rest_framework import serializers

from .models import Calendar, CalendarShare, Event
from .recurrence import validate_rrule


class CalendarSerializer(serializers.ModelSerializer):
    owner = serializers.CharField(source="owner.username", read_only=True)
    is_owner = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    has_feed = serializers.SerializerMethodField()
    shared_with = serializers.SerializerMethodField()

    class Meta:
        model = Calendar
        fields = ["id", "name", "color", "owner", "is_owner", "can_edit", "has_feed", "shared_with", "created_at"]
        read_only_fields = ["id", "owner", "created_at"]

    def _user(self):
        return self.context["request"].user

    def get_is_owner(self, obj):
        return obj.owner_id == self._user().pk

    def get_can_edit(self, obj):
        if obj.owner_id == self._user().pk:
            return True
        return obj.shares.filter(user=self._user(), can_edit=True).exists()

    def get_has_feed(self, obj):
        return bool(obj.feed_token_hash) if obj.owner_id == self._user().pk else False

    def get_shared_with(self, obj):
        if obj.owner_id != self._user().pk:
            return []
        return [{"username": s.user.username, "can_edit": s.can_edit} for s in obj.shares.select_related("user")]


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = [
            "id",
            "calendar",
            "title",
            "description",
            "location",
            "start",
            "end",
            "all_day",
            "rrule",
            "reminder_minutes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        start = attrs.get("start", getattr(self.instance, "start", None))
        end = attrs.get("end", getattr(self.instance, "end", None))
        if start and end and end < start:
            raise serializers.ValidationError({"end": ["Кінець раніше за початок."]})
        if "rrule" in attrs:
            attrs["rrule"] = validate_rrule(attrs["rrule"], start)
        if attrs.get("reminder_minutes") is not None and attrs["reminder_minutes"] > 60 * 24 * 30:
            raise serializers.ValidationError({"reminder_minutes": ["Максимум 30 днів."]})
        return attrs


class CalendarShareSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=40)
    can_edit = serializers.BooleanField(default=False)


class CalendarShareModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = CalendarShare
        fields = ["calendar", "user", "can_edit"]
