from apps.core.realtime import push_to_user

from .models import Participant


def member_or_none(user, conversation_id):
    return (
        Participant.objects.select_related("conversation")
        .filter(conversation_id=conversation_id, user=user)
        .first()
    )


def broadcast(conversation, event: str, payload: dict, exclude_user_id=None):
    for user_id in conversation.participants.values_list("user_id", flat=True):
        if exclude_user_id is not None and user_id == exclude_user_id:
            continue
        push_to_user(user_id, event, payload)
