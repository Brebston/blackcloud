"""Фото профілю.

Потік: POST (≤ 5 МБ, тип за вмістом — PNG/JPEG/WebP/HEIC) → оригінал зашифровано (Fernet)
у UserAvatar.pending → Celery-завдання на worker надсилає його в ізольований сервіс
thumbnailer (без секретів і мережі) → квадрат 256×256 WebP без EXIF зберігається в БД,
оригінал стирається. Веб-процес недовірені зображення не декодує.

Віддача: GET /api/users/<uuid>/avatar/ — будь-якому користувачу з сесією; приватний кеш,
ETag, nosniff, CSP sandbox. Пошук для веб-пошти: GET /api/users/avatars/?emails=…
"""

import logging
import time
import uuid

from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.http import HttpResponse, HttpResponseNotModified
from django.utils.translation import gettext as _
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core import crypto
from apps.core.audit import audit
from apps.core.realtime import push_to_user

from .models import User, UserAvatar

logger = logging.getLogger("blackcloud")

MAX_AVATAR_BYTES = 5 * 1024 * 1024
ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"}
MAX_LOOKUP_EMAILS = 100
CACHE_SECONDS = 24 * 3600


def _encrypt(data: bytes) -> bytes:
    return crypto._fernet().encrypt(data)


def _decrypt(token: bytes) -> bytes:
    return crypto._fernet().decrypt(bytes(token))


def _status(user) -> dict:
    av = UserAvatar.objects.filter(user=user).only("pending_id", "failed").first()
    if av is not None and av.pending_id:
        state = "processing"
    elif user.avatar_version:
        state = "ready"
    elif av is not None and av.failed:
        state = "failed"
    else:
        state = "none"
    return {"status": state, "avatar_url": user.avatar_url}


def process_avatar(user_id: str, pending_id: str) -> bool:
    """Виконується на worker: оригінал → thumbnailer → готове фото. True, якщо вдалося."""
    from apps.storage import thumbnails, thumbrender

    av = UserAvatar.objects.filter(user_id=user_id, pending_id=pending_id).first()
    if av is None:
        return False  # уже оброблено або замінено новішим завантаженням
    try:
        raw = _decrypt(av.pending)
        out = thumbnails.render("avatar", raw)
        if not thumbrender.is_webp(out):
            raise ValueError("bad output")
    except Exception as exc:  # пошкоджене чи підозріле зображення — без фото
        logger.info("avatar rejected for %s: %s", user_id, exc.__class__.__name__)
        UserAvatar.objects.filter(user_id=user_id, pending_id=pending_id).update(pending=b"", pending_id=None, failed=True)
        push_to_user(user_id, "avatar.updated", {"status": "failed"})
        return False
    with transaction.atomic():
        updated = UserAvatar.objects.filter(user_id=user_id, pending_id=pending_id).update(
            image=out, content_type="image/webp", pending=b"", pending_id=None, failed=False
        )
        if updated:
            # Версія зростає монотонно (і після видалення): старий URL з кешу браузера не повернеться
            User.objects.filter(pk=user_id).update(
                avatar_version=Greatest(F("avatar_version") + 1, Value(int(time.time())))
            )
    if updated:
        push_to_user(user_id, "avatar.updated", {"status": "ready"})
    return bool(updated)


class MyAvatarView(APIView):
    """GET — стан; POST — завантажити; DELETE — видалити."""

    parser_classes = [MultiPartParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "avatar"

    def get_throttles(self):
        return super().get_throttles() if self.request.method == "POST" else []

    def get(self, request):
        return Response(_status(request.user))

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": [_("Виберіть зображення.")]})
        if upload.size > MAX_AVATAR_BYTES:
            raise ValidationError({"file": [_("Фото завелике (максимум 5 МБ).")]})
        data = upload.read(MAX_AVATAR_BYTES + 1)
        if not data or len(data) > MAX_AVATAR_BYTES:
            raise ValidationError({"file": [_("Фото завелике (максимум 5 МБ).")]})
        import magic

        mime = magic.from_buffer(data[:65536], mime=True)
        if mime not in ALLOWED_MIME:
            raise ValidationError({"file": [_("Підтримуються лише PNG, JPEG, WebP і HEIC.")]})
        pending_id = uuid.uuid4()
        UserAvatar.objects.update_or_create(
            user=request.user, defaults={"pending": _encrypt(data), "pending_id": pending_id, "failed": False}
        )
        from .tasks import process_avatar_task

        transaction.on_commit(lambda: process_avatar_task.delay(str(request.user.pk), str(pending_id)))
        audit(request, "avatar.uploaded", size=len(data), mime=mime)
        request.user.refresh_from_db(fields=["avatar_version"])
        return Response(_status(request.user), status=202)

    def delete(self, request):
        UserAvatar.objects.filter(user=request.user).delete()
        User.objects.filter(pk=request.user.pk).update(avatar_version=0)
        request.user.avatar_version = 0
        audit(request, "avatar.deleted")
        push_to_user(request.user.pk, "avatar.updated", {"status": "none"})
        return Response(status=204)


class UserAvatarView(APIView):
    """Готове фото будь-якого користувача (лише для тих, хто увійшов)."""

    def get(self, request, pk):
        user = User.objects.filter(pk=pk).only("id", "avatar_version").first()
        if user is None or not user.avatar_version:
            return HttpResponse(status=404)
        etag = f'"{user.pk.hex}-{user.avatar_version}"'
        if etag in [t.strip() for t in request.headers.get("If-None-Match", "").split(",")]:
            response = HttpResponseNotModified()
        else:
            av = UserAvatar.objects.filter(user_id=user.pk).only("image", "content_type").first()
            if av is None or not av.image or av.content_type != "image/webp":
                return HttpResponse(status=404)
            response = HttpResponse(bytes(av.image), content_type=av.content_type)
            response["Content-Disposition"] = 'inline; filename="avatar.webp"'
        response["ETag"] = etag
        response["Cache-Control"] = f"private, max-age={CACHE_SECONDS}"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        response["Cross-Origin-Resource-Policy"] = "same-origin"
        return response


class AvatarLookupView(APIView):
    """{адреса: url} для локальних скриньок/псевдонімів, власники яких мають фото (для веб-пошти)."""

    def get(self, request):
        raw = request.query_params.get("emails") or ""
        emails = []
        for item in raw.split(","):
            item = item.strip().lower()
            if item and "@" in item and len(item) <= 254 and item not in emails:
                emails.append(item)
        if len(emails) > MAX_LOOKUP_EMAILS:
            raise ValidationError({"emails": [_("Максимум %(n)s адрес.") % {"n": MAX_LOOKUP_EMAILS}]})
        if not emails:
            return Response({})
        from apps.mail.models import Alias, Mailbox

        owners: dict[str, object] = {}  # адреса → user_id
        parsed = [e.rsplit("@", 1) for e in emails]
        boxes = Mailbox.objects.filter(
            active=True,
            local_part__in={lp for lp, _d in parsed},
            domain__name__in={d for _lp, d in parsed},
            domain__active=True,
        ).select_related("domain")
        by_address = {mb.address: mb.user_id for mb in boxes}
        for e in emails:
            if e in by_address:
                owners[e] = by_address[e]
        rest = [e for e in emails if e not in owners]
        if rest:
            aliases = Alias.objects.filter(source__in=rest, active=True)
            dest_boxes = {}
            dests = {a.destination for a in aliases if not a.owner_id}
            if dests:
                dparsed = [d.rsplit("@", 1) for d in dests if "@" in d]
                for mb in Mailbox.objects.filter(
                    active=True,
                    local_part__in={lp for lp, _d in dparsed},
                    domain__name__in={d for _lp, d in dparsed},
                ).select_related("domain"):
                    dest_boxes[mb.address] = mb.user_id
            for a in aliases:
                uid = a.owner_id or dest_boxes.get(a.destination)
                if uid:
                    owners[a.source] = uid
        users = {
            u.pk: u
            for u in User.objects.filter(pk__in=set(owners.values()), is_active=True, avatar_version__gt=0).only(
                "id", "avatar_version"
            )
        }
        return Response({e: users[uid].avatar_url for e, uid in owners.items() if uid in users})
