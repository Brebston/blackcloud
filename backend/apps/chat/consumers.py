import json
import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from apps.core.realtime import session_group, user_group

MAX_FRAME = 4096


class EventsConsumer(AsyncWebsocketConsumer):
    """Єдиний WebSocket користувача: нові повідомлення, сповіщення, статуси файлів."""

    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated or not user.is_active:
            await self.close(code=4401)
            return
        self.user = user
        self.group = user_group(user.pk)
        session = self.scope.get("session")
        key = getattr(session, "session_key", None)
        self.session_group = session_group(key) if key else None
        self._last_typing = 0.0
        await self.channel_layer.group_add(self.group, self.channel_name)
        if self.session_group:
            await self.channel_layer.group_add(self.session_group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)
        if getattr(self, "session_group", None):
            await self.channel_layer.group_discard(self.session_group, self.channel_name)

    async def force_close(self, event):
        """Сесію завершено на сервері — з'єднання закривається негайно."""
        await self.close(code=4401)

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data or len(text_data) > MAX_FRAME:
            return
        try:
            msg = json.loads(text_data)
        except ValueError:
            return
        if msg.get("type") == "typing":
            now = time.monotonic()
            if now - self._last_typing < 2:
                return
            self._last_typing = now
            await self._broadcast_typing(str(msg.get("conversation", ""))[:36])
        elif msg.get("type") == "ping":
            await self.send(text_data=json.dumps({"event": "pong"}))

    @database_sync_to_async
    def _typing_targets(self, conversation_id):
        from .models import Participant

        if not Participant.objects.filter(conversation_id=conversation_id, user=self.user).exists():
            return []
        return list(
            Participant.objects.filter(conversation_id=conversation_id)
            .exclude(user=self.user)
            .values_list("user_id", flat=True)
        )

    async def _broadcast_typing(self, conversation_id):
        try:
            targets = await self._typing_targets(conversation_id)
        except Exception:
            return
        for uid in targets:
            await self.channel_layer.group_send(
                user_group(uid),
                {
                    "type": "push",
                    "event": "typing",
                    "payload": {"conversation": conversation_id, "username": self.user.username},
                },
            )

    async def push(self, event):
        await self.send(text_data=json.dumps({"event": event["event"], "payload": event["payload"]}, default=str))
