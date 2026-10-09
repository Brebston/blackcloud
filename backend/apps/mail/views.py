import uuid

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.db.models import F
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import Preferences
from apps.accounts.views import _require_password
from apps.core.audit import audit
from apps.core.tickets import issue_ticket, peek_ticket
from apps.storage.services import content_disposition

from . import imap
from .models import ConfidentialMessage, Mailbox, Signature
from .passwords import dovecot_hash, generate_client_password


def _mailbox(request) -> Mailbox:
    """Скринька, з якою працює запит. Користувач може мати кілька скриньок;
    обрана передається як ?mailbox=<id> (або полем mailbox у тілі). Завжди
    перевіряється, що скринька належить саме цьому користувачу."""
    qs = Mailbox.objects.select_related("domain").filter(user=request.user, active=True)
    wanted = request.query_params.get("mailbox") or (request.data.get("mailbox") if hasattr(request, "data") else None)
    if wanted:
        try:
            mb = qs.filter(pk=uuid.UUID(str(wanted))).first()
        except ValueError:
            mb = None
    else:
        mb = qs.order_by("created_at").first()
    if mb is None:
        raise NotFound("Поштову скриньку не знайдено. Зверніться до адміністратора.")
    return mb


def _mailbox_info(mb: Mailbox) -> dict:
    return {"id": str(mb.pk), "address": mb.address, "display_name": mb.display_name}


def _folder(request) -> str:
    folder = request.query_params.get("folder") or "INBOX"
    if len(folder) > 200:
        raise ValidationError({"folder": ["Задовга назва теки."]})
    return folder


class MailboxesView(APIView):
    """Список скриньок користувача (для перемикача у веб-пошті)."""

    def get(self, request):
        boxes = Mailbox.objects.select_related("domain").filter(user=request.user, active=True).order_by("created_at")
        return Response([_mailbox_info(mb) for mb in boxes])


class MailboxView(APIView):
    def get(self, request):
        mb = _mailbox(request)
        return Response(
            {
                "id": str(mb.pk),
                "display_name": mb.display_name,
                "address": mb.address,
                "quota_mb": mb.quota_mb,
                "client_password_set_at": mb.client_password_set_at,
                "imap": {"host": settings.MAIL_HOSTNAME, "port": 993, "security": "SSL/TLS"},
                "smtp": {"host": settings.MAIL_HOSTNAME, "port": 465, "security": "SSL/TLS"},
            }
        )


class ClientPasswordView(APIView):
    """Генерує новий пароль для поштових клієнтів (показується один раз)."""

    def post(self, request):
        _require_password(request, request.data.get("password") or "")
        mb = _mailbox(request)
        if request.data.get("revoke"):
            mb.client_password_hash = ""
            mb.client_password_set_at = None
            mb.save(update_fields=["client_password_hash", "client_password_set_at"])
            audit(request, "mail.client_password_revoked")
            return Response({"status": "revoked"})
        password = generate_client_password()
        mb.client_password_hash = dovecot_hash(password)
        mb.client_password_set_at = timezone.now()
        mb.save(update_fields=["client_password_hash", "client_password_set_at"])
        audit(request, "mail.client_password_set")
        return Response({"username": mb.address, "password": password})


class FoldersView(APIView):
    def get(self, request):
        return Response(imap.list_folders(_mailbox(request).address))


class MessagesView(APIView):
    def get(self, request):
        mb = _mailbox(request)
        try:
            page = int(request.query_params.get("page", 1))
        except ValueError:
            page = 1
        return Response(
            imap.list_messages(mb.address, _folder(request), page=page, query=request.query_params.get("q", ""))
        )


class MessageDetailView(APIView):
    def get(self, request, uid):
        return Response(imap.get_message(_mailbox(request).address, _folder(request), uid))

    def delete(self, request, uid):
        imap.delete_message(_mailbox(request).address, _folder(request), uid)
        return Response(status=204)

    def patch(self, request, uid):
        mb = _mailbox(request)
        folder = request.data.get("folder") or "INBOX"
        if "seen" in request.data:
            imap.set_flag(mb.address, folder, uid, "\\Seen", bool(request.data["seen"]))
        if "flagged" in request.data:
            imap.set_flag(mb.address, folder, uid, "\\Flagged", bool(request.data["flagged"]))
        if request.data.get("move_to"):
            imap.move_message(mb.address, folder, uid, str(request.data["move_to"])[:200])
        return Response({"status": "ok"})


class RenderView(APIView):
    """HTML листа для sandbox-iframe. Окремий CSP: без скриптів, без мережі."""

    def get(self, request):
        mb = _mailbox(request)
        prefs = Preferences.objects.get_or_create(user=request.user)[0]
        allow_remote = prefs.mail_load_remote_images or request.query_params.get("remote") == "1"
        html = imap.render_html(mb.address, _folder(request), request.query_params.get("uid", ""), allow_remote=allow_remote)
        return _sandboxed_html(html, allow_remote=allow_remote)


def _sandboxed_html(html: str, *, allow_remote: bool) -> HttpResponse:
    img_src = "data: https:" if allow_remote else "data:"
    doc = (
        "<!doctype html><html><head><meta charset='utf-8'><base target='_blank'>"
        "<style>body{font-family:system-ui,sans-serif;margin:12px;color:#1a1a1a;background:#fff;word-wrap:break-word}"
        "img{max-width:100%;height:auto}</style></head><body>" + html + "</body></html>"
    )
    response = HttpResponse(doc, content_type="text/html; charset=utf-8")
    response["Content-Security-Policy"] = (
        f"default-src 'none'; style-src 'unsafe-inline'; img-src {img_src}; font-src data:; "
        "frame-ancestors 'self'; form-action 'none'; base-uri 'none'; "
        "sandbox allow-popups allow-popups-to-escape-sandbox"
    )
    response["X-Frame-Options"] = "SAMEORIGIN"
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "private, no-store"
    return response


class AttachmentView(APIView):
    def get(self, request, uid, index):
        filename, _ctype, payload = imap.get_attachment(_mailbox(request).address, _folder(request), uid, int(index))
        response = HttpResponse(payload, content_type="application/octet-stream")
        response["Content-Disposition"] = content_disposition("attachment", filename)
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response


class SendView(APIView):
    parser_classes = [MultiPartParser, JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "mail_send"

    def post(self, request):
        mb = _mailbox(request)
        attachments = request.FILES.getlist("attachments") if hasattr(request, "FILES") else []
        if len(attachments) > 20:
            raise ValidationError({"attachments": ["Максимум 20 вкладень."]})
        result = imap.send_message(request.user, mb, request.data, attachments)
        audit(
            request,
            "mail.sent",
            mailbox=mb.address,
            recipients=len((request.data.get("to") or "").split(",")),
            confidential="confidential" in result,
        )
        return Response(result, status=201)


# ═══════════════════════════ Підписи ═══════════════════════════

MAX_SIGNATURES = 20


class SignatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = Signature
        fields = ["id", "name", "html", "is_default", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_html(self, value):
        from .sanitize import sanitize_outgoing_html

        if len(value) > 200_000:
            raise serializers.ValidationError("Підпис завеликий (максимум ~200 КБ разом із зображеннями).")
        return sanitize_outgoing_html(value)


def _apply_default(user, sig: Signature):
    if sig.is_default:
        Signature.objects.filter(user=user, is_default=True).exclude(pk=sig.pk).update(is_default=False)


class SignaturesView(APIView):
    def get(self, request):
        return Response(SignatureSerializer(Signature.objects.filter(user=request.user), many=True).data)

    def post(self, request):
        if Signature.objects.filter(user=request.user).count() >= MAX_SIGNATURES:
            raise ValidationError({"detail": f"Максимум {MAX_SIGNATURES} підписів."})
        ser = SignatureSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        sig = ser.save(user=request.user)
        _apply_default(request.user, sig)
        return Response(SignatureSerializer(sig).data, status=201)


class SignatureDetailView(APIView):
    def _get(self, request, pk) -> Signature:
        sig = Signature.objects.filter(pk=pk, user=request.user).first()
        if sig is None:
            raise NotFound()
        return sig

    def patch(self, request, pk):
        sig = self._get(request, pk)
        ser = SignatureSerializer(sig, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        sig = ser.save()
        _apply_default(request.user, sig)
        return Response(SignatureSerializer(sig).data)

    def delete(self, request, pk):
        self._get(request, pk).delete()
        return Response(status=204)


# ═══════════════════════════ Конфіденційні листи ═══════════════════════════


class ConfidentialListView(APIView):
    def get(self, request):
        items = ConfidentialMessage.objects.filter(sender=request.user)[:100]
        return Response(
            [
                {
                    "id": str(m.pk),
                    "subject": m.subject,
                    "recipients": m.recipients,
                    "from_address": m.from_address,
                    "created_at": m.created_at,
                    "expires_at": m.expires_at,
                    "revoked_at": m.revoked_at,
                    "active": m.is_active,
                    "view_count": m.view_count,
                    "has_passcode": bool(m.passcode_hash),
                }
                for m in items
            ]
        )


class ConfidentialRevokeView(APIView):
    def post(self, request, pk):
        updated = ConfidentialMessage.objects.filter(pk=pk, sender=request.user, revoked_at__isnull=True).update(
            revoked_at=timezone.now(), html_encrypted=""
        )
        if not updated:
            raise NotFound()
        audit(request, "mail.confidential_revoked", target=str(pk))
        return Response({"status": "revoked"})


def _confidential_by_token(token) -> ConfidentialMessage:
    from .confidential import hash_token

    if not isinstance(token, str) or not token or len(token) > 100:
        raise NotFound("Лист недоступний.")
    msg = ConfidentialMessage.objects.filter(token_hash=hash_token(token)).first()
    if msg is None or not msg.is_active:
        raise NotFound("Лист недоступний: термін дії минув або відправник відкликав доступ.")
    return msg


class PublicConfidentialView(APIView):
    """Відкриття конфіденційного листа отримувачем (без входу; токен — у тілі запиту)."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_link"

    def post(self, request):
        msg = _confidential_by_token(request.data.get("token"))
        if msg.passcode_hash:
            key = f"conf-fail:{msg.pk}"
            if (cache.get(key) or 0) >= 10:
                return Response({"detail": "Забагато невдалих спроб. Спробуйте пізніше."}, status=429)
            passcode = str(request.data.get("passcode") or "")
            if not passcode:
                return Response({"requires_passcode": True, "subject": "", "from": msg.from_address})
            if not check_password(passcode, msg.passcode_hash):
                if not cache.add(key, 1, 3600):
                    cache.incr(key)
                return Response({"detail": "Невірний код доступу."}, status=403)
        ConfidentialMessage.objects.filter(pk=msg.pk).update(view_count=F("view_count") + 1)
        ticket = issue_ticket("conf-render", {"id": str(msg.pk)}, 300)
        return Response(
            {
                "requires_passcode": False,
                "subject": msg.subject,
                "from": msg.from_address,
                "expires_at": msg.expires_at,
                "render_url": f"/api/public/confidential/r/{ticket}/",
            }
        )


class PublicConfidentialRenderView(APIView):
    """HTML конфіденційного листа для sandbox-iframe (той самий жорсткий CSP, що й у веб-пошті)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, ticket):
        from apps.core.crypto import decrypt_str

        from .sanitize import sanitize_html

        data = peek_ticket("conf-render", ticket)
        msg = ConfidentialMessage.objects.filter(pk=(data or {}).get("id")).first() if data else None
        if msg is None or not msg.is_active:
            raise NotFound()
        html = sanitize_html(decrypt_str(msg.html_encrypted), inline_images={}, allow_remote=False)
        return _sandboxed_html(html, allow_remote=False)
