from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import Preferences
from apps.accounts.views import _require_password
from apps.core.audit import audit
from apps.storage.services import content_disposition

from . import imap
from .models import Mailbox
from .passwords import dovecot_hash, generate_client_password


def _mailbox(user) -> Mailbox:
    mb = Mailbox.objects.select_related("domain").filter(user=user, active=True).first()
    if mb is None:
        raise NotFound("У вас немає поштової скриньки. Зверніться до адміністратора.")
    return mb


def _folder(request) -> str:
    folder = request.query_params.get("folder") or "INBOX"
    if len(folder) > 200:
        raise ValidationError({"folder": ["Задовга назва теки."]})
    return folder


class MailboxView(APIView):
    def get(self, request):
        mb = _mailbox(request.user)
        return Response(
            {
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
        mb = _mailbox(request.user)
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
        return Response(imap.list_folders(_mailbox(request.user).address))


class MessagesView(APIView):
    def get(self, request):
        mb = _mailbox(request.user)
        try:
            page = int(request.query_params.get("page", 1))
        except ValueError:
            page = 1
        return Response(
            imap.list_messages(mb.address, _folder(request), page=page, query=request.query_params.get("q", ""))
        )


class MessageDetailView(APIView):
    def get(self, request, uid):
        return Response(imap.get_message(_mailbox(request.user).address, _folder(request), uid))

    def delete(self, request, uid):
        imap.delete_message(_mailbox(request.user).address, _folder(request), uid)
        return Response(status=204)

    def patch(self, request, uid):
        mb = _mailbox(request.user)
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
        mb = _mailbox(request.user)
        prefs = Preferences.objects.get_or_create(user=request.user)[0]
        allow_remote = prefs.mail_load_remote_images or request.query_params.get("remote") == "1"
        html = imap.render_html(mb.address, _folder(request), request.query_params.get("uid", ""), allow_remote=allow_remote)
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
        filename, _ctype, payload = imap.get_attachment(_mailbox(request.user).address, _folder(request), uid, int(index))
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
        mb = _mailbox(request.user)
        attachments = request.FILES.getlist("attachments") if hasattr(request, "FILES") else []
        if len(attachments) > 20:
            raise ValidationError({"attachments": ["Максимум 20 вкладень."]})
        message_id = imap.send_message(request.user, mb, request.data, attachments)
        audit(request, "mail.sent", recipients=len((request.data.get("to") or "").split(",")))
        return Response({"message_id": message_id}, status=201)
