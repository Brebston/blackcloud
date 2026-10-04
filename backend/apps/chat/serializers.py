from rest_framework import serializers

from .models import Conversation, Message


class MessageSerializer(serializers.ModelSerializer):
    sender = serializers.CharField(source="sender.username", read_only=True, default=None)
    file_info = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ["id", "conversation", "sender", "body", "file", "file_info", "created_at", "edited_at", "deleted"]
        read_only_fields = fields

    def get_file_info(self, obj):
        if obj.file_id and obj.file and obj.file.deleted_at is None:
            return {"id": str(obj.file.id), "name": obj.file.name, "size": obj.file.size}
        return None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.deleted:
            data["body"] = ""
            data["file_info"] = None
        return data


class ConversationSerializer(serializers.ModelSerializer):
    participants = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread = serializers.SerializerMethodField()
    display_title = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "is_group",
            "title",
            "display_title",
            "participants",
            "last_message",
            "last_message_at",
            "unread",
            "created_at",
        ]

    def _me(self):
        return self.context["request"].user

    def get_participants(self, obj):
        return [
            {"username": p.user.username, "display_name": p.user.display_name, "is_admin": p.is_admin}
            for p in obj.participants.all()
        ]

    def get_display_title(self, obj):
        if obj.title:
            return obj.title
        others = [p.user.display_name or p.user.username for p in obj.participants.all() if p.user_id != self._me().pk]
        return ", ".join(others) or "Нотатки"

    def get_last_message(self, obj):
        msg = obj.messages.filter(deleted=False).order_by("-created_at").select_related("sender").first()
        if not msg:
            return None
        return {"sender": msg.sender.username if msg.sender else None, "body": msg.body[:120], "created_at": msg.created_at}

    def get_unread(self, obj):
        me = next((p for p in obj.participants.all() if p.user_id == self._me().pk), None)
        qs = obj.messages.filter(deleted=False).exclude(sender=self._me())
        if me and me.last_read_at:
            qs = qs.filter(created_at__gt=me.last_read_at)
        return qs.count()


class CreateConversationSerializer(serializers.Serializer):
    usernames = serializers.ListField(child=serializers.CharField(max_length=40), min_length=1, max_length=50)
    title = serializers.CharField(max_length=100, required=False, allow_blank=True)


class SendMessageSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=10000, allow_blank=True, required=False, default="")
    file = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        if not attrs.get("body", "").strip() and not attrs.get("file"):
            raise serializers.ValidationError("Порожнє повідомлення.")
        return attrs
