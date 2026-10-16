"""Адресна книга: /api/mail/contacts/ (особисті контакти) і підказки одержувачів."""

import re

from django.utils.translation import gettext as _
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import contacts
from .models import Contact

MAX_CONTACTS = 5000
MAX_ITEMS = 10
PHONE_RE = re.compile(r"^[0-9+()\-. /]{3,40}$")


class EmailItem(serializers.Serializer):
    address = serializers.EmailField(max_length=320)
    label = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")


class PhoneItem(serializers.Serializer):
    number = serializers.CharField(max_length=40)
    label = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")

    def validate_number(self, value):
        value = value.strip()
        if not PHONE_RE.match(value):
            raise serializers.ValidationError(_("Невірний номер телефону."))
        return value


class ContactSerializer(serializers.ModelSerializer):
    emails = EmailItem(many=True, required=False)
    phones = PhoneItem(many=True, required=False)
    display_name = serializers.CharField(read_only=True)

    class Meta:
        model = Contact
        fields = [
            "id", "first_name", "last_name", "nickname", "display_name", "emails", "phones", "company", "job_title",
            "birthday", "address", "notes", "starred", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_emails(self, value):
        if len(value) > MAX_ITEMS:
            raise serializers.ValidationError(_("Забагато адрес (максимум %(n)s).") % {"n": MAX_ITEMS})
        out, seen = [], set()
        for item in value:
            address = item["address"].strip().lower()
            if address not in seen:
                seen.add(address)
                out.append({"address": address, "label": item.get("label", "").strip()})
        return out

    def validate_phones(self, value):
        if len(value) > MAX_ITEMS:
            raise serializers.ValidationError(_("Забагато номерів (максимум %(n)s).") % {"n": MAX_ITEMS})
        return [{"number": p["number"], "label": p.get("label", "").strip()} for p in value]

    def validate(self, attrs):
        inst = self.instance
        names = [attrs.get(f, getattr(inst, f, "") if inst else "") for f in ("first_name", "last_name", "nickname", "company")]
        emails = attrs.get("emails", inst.emails if inst else [])
        if not any(n.strip() for n in names) and not emails:
            raise ValidationError({"detail": _("Вкажіть ім'я або email.")})
        for f in ("first_name", "last_name", "nickname", "company", "job_title", "address"):
            if f in attrs:
                attrs[f] = attrs[f].strip()
        return attrs


class ContactsView(APIView):
    def get(self, request):
        qs = Contact.objects.filter(owner=request.user)
        q = (request.query_params.get("q") or "").strip().lower()
        if q:
            qs = qs.filter(search_text__contains=q[:100])
        if request.query_params.get("starred") == "1":
            qs = qs.filter(starred=True)
        return Response(ContactSerializer(qs[:MAX_CONTACTS], many=True).data)

    def post(self, request):
        if Contact.objects.filter(owner=request.user).count() >= MAX_CONTACTS:
            raise ValidationError({"detail": _("Досягнуто максимуму контактів (%(n)s).") % {"n": MAX_CONTACTS}})
        ser = ContactSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        contact = ser.save(owner=request.user)
        return Response(ContactSerializer(contact).data, status=201)


class ContactDetailView(APIView):
    def _get(self, request, pk) -> Contact:
        obj = Contact.objects.filter(pk=pk, owner=request.user).first()
        if obj is None:
            raise NotFound()
        return obj

    def get(self, request, pk):
        return Response(ContactSerializer(self._get(request, pk)).data)

    def patch(self, request, pk):
        ser = ContactSerializer(self._get(request, pk), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        return Response(ContactSerializer(ser.save()).data)

    def delete(self, request, pk):
        self._get(request, pk).delete()
        return Response(status=204)


class SuggestView(APIView):
    """GET ?q=ол — до 8 ймовірних одержувачів (контакти, листування, користувачі)."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "mail_suggest"

    def get(self, request):
        return Response(contacts.suggest(request.user, request.query_params.get("q", "")))


class RecentRecipientDeleteView(APIView):
    """Прибрати адресу з підказок «кому писали» (як «Видалити з підказок» у Gmail)."""

    def delete(self, request):
        address = (request.query_params.get("address") or "").strip().lower()
        from .models import RecipientStat

        RecipientStat.objects.filter(owner=request.user, address=address).delete()
        return Response(status=204)
