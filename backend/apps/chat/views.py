from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.audit import audit
from apps.storage.models import File, Share

from .emoji import is_single_emoji
from .models import Conversation, Message, Participant, Reaction
from .serializers import (
    ConversationSerializer,
    CreateConversationSerializer,
    MessageSerializer,
    SendMessageSerializer,
    reactions_summary,
)
from .services import broadcast, member_or_none

User = get_user_model()
PAGE = 50
EDIT_WINDOW_HOURS = 48
MAX_REACTION_KINDS = 30  # різних емоджі на одне повідомлення
MAX_USER_REACTIONS = 10  # реакцій одного користувача на одне повідомлення


def _conversations_qs(user):
    return (
        Conversation.objects.filter(participants__user=user)
        .prefetch_related(Prefetch("participants", queryset=Participant.objects.select_related("user")))
        .distinct()
    )


def _reachable(me, users) -> bool:
    """Написати можна лише тим, хто дозволив пошук себе (discoverable),
    або з ким уже є спільна розмова. Інакше чат дозволяв би перебирати імена
    користувачів і писати тим, хто приховав себе."""
    if me.is_staff:
        return True
    known = set(
        Participant.objects.filter(conversation__participants__user=me)
        .exclude(user=me)
        .values_list("user_id", flat=True)
    )
    for u in users:
        if u.pk in known:
            continue
        prefs = getattr(u, "preferences", None)
        if prefs is None or not prefs.discoverable:
            return False
    return True


def _membership(user, pk) -> Participant:
    p = member_or_none(user, pk)
    if p is None:
        raise NotFound("Розмову не знайдено.")
    return p


class ConversationsView(APIView):
    def get(self, request):
        convs = _conversations_qs(request.user).order_by("-last_message_at", "-created_at")[:200]
        return Response(ConversationSerializer(convs, many=True, context={"request": request}).data)

    def post(self, request):
        ser = CreateConversationSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        names = {n.lower() for n in ser.validated_data["usernames"]} - {request.user.username}
        users = list(User.objects.filter(username__in=names, is_active=True).select_related("preferences"))
        if not users or len(users) != len(names) or not _reachable(request.user, users):
            # Однакова відповідь для «не існує» і «приховав себе»
            raise ValidationError({"usernames": ["Деяких користувачів не знайдено."]})
        title = ser.validated_data.get("title", "").strip()

        if len(users) == 1 and not title:
            other = users[0]
            key = ":".join(sorted([str(request.user.pk), str(other.pk)]))
            conv = Conversation.objects.filter(direct_key=key).first()
            if conv is None:
                try:
                    with transaction.atomic():
                        conv = Conversation.objects.create(created_by=request.user, direct_key=key)
                        Participant.objects.create(conversation=conv, user=request.user)
                        Participant.objects.create(conversation=conv, user=other)
                except IntegrityError:
                    conv = Conversation.objects.get(direct_key=key)
        else:
            with transaction.atomic():
                conv = Conversation.objects.create(created_by=request.user, is_group=True, title=title[:100])
                Participant.objects.create(conversation=conv, user=request.user, is_admin=True)
                for u in users:
                    Participant.objects.create(conversation=conv, user=u)
        conv = _conversations_qs(request.user).get(pk=conv.pk)
        data = ConversationSerializer(conv, context={"request": request}).data
        broadcast(conv, "conversation.updated", {"id": str(conv.pk)}, exclude_user_id=request.user.pk)
        return Response(data, status=201)


class ConversationDetailView(APIView):
    def get(self, request, pk):
        _membership(request.user, pk)
        conv = _conversations_qs(request.user).get(pk=pk)
        return Response(ConversationSerializer(conv, context={"request": request}).data)


class MessagesView(APIView):
    throttle_scope = "chat_send"

    def get_throttles(self):
        if self.request.method == "POST":
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def get(self, request, pk):
        _membership(request.user, pk)
        qs = (
            Message.objects.filter(conversation_id=pk)
            .select_related("sender", "file")
            .prefetch_related(Prefetch("reactions", queryset=Reaction.objects.select_related("user")))
        )
        before = parse_datetime(request.query_params.get("before") or "")
        if before:
            qs = qs.filter(created_at__lt=before)
        msgs = list(qs.order_by("-created_at")[:PAGE])
        msgs.reverse()
        return Response({"results": MessageSerializer(msgs, many=True).data, "has_more": len(msgs) == PAGE})

    def post(self, request, pk):
        me = _membership(request.user, pk)
        conv = me.conversation
        ser = SendMessageSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        file = None
        if ser.validated_data.get("file"):
            file = File.objects.filter(
                pk=ser.validated_data["file"], owner=request.user, deleted_at__isnull=True
            ).first()
            if file is None or not file.is_downloadable:
                raise ValidationError({"file": ["Файл недоступний."]})
        with transaction.atomic():
            msg = Message.objects.create(
                conversation=conv, sender=request.user, body=ser.validated_data.get("body", "").strip(), file=file
            )
            Conversation.objects.filter(pk=conv.pk).update(last_message_at=msg.created_at)
            Participant.objects.filter(pk=me.pk).update(last_read_at=msg.created_at)
            if file is not None:
                # Надаємо учасникам доступ на читання до вкладеного файлу
                for p in conv.participants.exclude(user=request.user).select_related("user"):
                    Share.objects.get_or_create(owner=request.user, recipient=p.user, file=file)
        data = MessageSerializer(msg).data
        broadcast(conv, "message.new", data)
        return Response(data, status=201)


class MessageDetailView(APIView):
    def _own(self, request, pk) -> Message:
        msg = Message.objects.select_related("conversation").filter(pk=pk, sender=request.user, deleted=False).first()
        if msg is None or member_or_none(request.user, msg.conversation_id) is None:
            raise NotFound()
        return msg

    def patch(self, request, pk):
        msg = self._own(request, pk)
        if (timezone.now() - msg.created_at).total_seconds() > EDIT_WINDOW_HOURS * 3600:
            raise PermissionDenied("Час на редагування минув.")
        body = (request.data.get("body") or "").strip()
        if not body or len(body) > 10000:
            raise ValidationError({"body": ["1–10000 символів."]})
        msg.body = body
        msg.edited_at = timezone.now()
        msg.save(update_fields=["body", "edited_at"])
        data = MessageSerializer(msg).data
        broadcast(msg.conversation, "message.updated", data)
        return Response(data)

    def delete(self, request, pk):
        msg = self._own(request, pk)
        msg.deleted = True
        msg.body = ""
        msg.save(update_fields=["deleted", "body"])
        broadcast(msg.conversation, "message.updated", MessageSerializer(msg).data)
        return Response(status=204)


class ReactionView(APIView):
    """POST {emoji} — поставити реакцію або зняти, якщо вона вже є (перемикач)."""

    throttle_scope = "chat_send"
    throttle_classes = [ScopedRateThrottle]

    def post(self, request, pk):
        emoji = request.data.get("emoji")
        if not is_single_emoji(emoji):
            raise ValidationError({"emoji": ["Потрібен один емоджі."]})
        msg = Message.objects.select_related("conversation").filter(pk=pk, deleted=False).first()
        if msg is None or member_or_none(request.user, msg.conversation_id) is None:
            raise NotFound()
        with transaction.atomic():
            existing = Reaction.objects.filter(message=msg, user=request.user, emoji=emoji).first()
            if existing:
                existing.delete()
            else:
                if Reaction.objects.filter(message=msg, user=request.user).count() >= MAX_USER_REACTIONS:
                    raise ValidationError({"emoji": ["Забагато реакцій на одне повідомлення."]})
                kinds = set(Reaction.objects.filter(message=msg).values_list("emoji", flat=True))
                if emoji not in kinds and len(kinds) >= MAX_REACTION_KINDS:
                    raise ValidationError({"emoji": ["Забагато різних реакцій."]})
                try:
                    with transaction.atomic():
                        Reaction.objects.create(message=msg, user=request.user, emoji=emoji)
                except IntegrityError:
                    pass  # паралельний подвійний клік
        msg = Message.objects.prefetch_related(
            Prefetch("reactions", queryset=Reaction.objects.select_related("user"))
        ).get(pk=msg.pk)
        payload = {"id": str(msg.pk), "conversation": str(msg.conversation_id), "reactions": reactions_summary(msg)}
        broadcast(msg.conversation, "message.reactions", payload)
        return Response(payload)


class ReadView(APIView):
    def post(self, request, pk):
        me = _membership(request.user, pk)
        me.last_read_at = timezone.now()
        me.save(update_fields=["last_read_at"])
        return Response({"status": "ok"})


class LeaveView(APIView):
    def post(self, request, pk):
        me = _membership(request.user, pk)
        conv = me.conversation
        if not conv.is_group:
            raise ValidationError({"detail": "З особистого діалогу не можна вийти."})
        me.delete()
        if not conv.participants.exists():
            conv.delete()
        else:
            if not conv.participants.filter(is_admin=True).exists():
                first = conv.participants.order_by("joined_at").first()
                first.is_admin = True
                first.save(update_fields=["is_admin"])
            broadcast(conv, "conversation.updated", {"id": str(conv.pk)})
        return Response(status=204)


class MembersView(APIView):
    def post(self, request, pk):
        me = _membership(request.user, pk)
        conv = me.conversation
        if not conv.is_group or not me.is_admin:
            raise PermissionDenied("Лише адміністратор групи може додавати учасників.")
        user = (
            User.objects.filter(username=(request.data.get("username") or "").lower(), is_active=True)
            .select_related("preferences")
            .first()
        )
        if user is None or not _reachable(request.user, [user]):
            raise ValidationError({"username": ["Користувача не знайдено."]})
        if conv.participants.count() >= 200:
            raise ValidationError({"detail": "Забагато учасників."})
        Participant.objects.get_or_create(conversation=conv, user=user)
        audit(request, "chat.member_added", target=str(conv.pk), member=user.username)
        broadcast(conv, "conversation.updated", {"id": str(conv.pk)})
        return Response(status=status.HTTP_201_CREATED)
