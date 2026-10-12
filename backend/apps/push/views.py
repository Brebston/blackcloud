from django.db import transaction
from django.utils.translation import gettext as _
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from . import webpush
from .models import MAX_SUBSCRIPTIONS_PER_USER, PushSubscription
from .service import send_to_user, vapid_public_key


PREF_FIELDS = ("notify_mail", "notify_chat", "notify_other", "show_previews")


class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PushSubscription
        fields = [
            "id", "endpoint", "user_agent", "notify_mail", "notify_chat", "notify_other", "show_previews",
            "created_at", "last_used_at",
        ]


class SubscribeSerializer(serializers.Serializer):
    endpoint = serializers.CharField(max_length=1000)
    p256dh = serializers.CharField(max_length=100)
    auth = serializers.CharField(max_length=40)
    notify_mail = serializers.BooleanField(required=False)
    notify_chat = serializers.BooleanField(required=False)
    notify_other = serializers.BooleanField(required=False)
    show_previews = serializers.BooleanField(required=False)
    # Попередня адреса цього ж браузера (подія pushsubscriptionchange): налаштування переносяться
    replaces = serializers.CharField(max_length=1000, required=False)

    def validate_endpoint(self, value):
        if not webpush.endpoint_allowed(value):
            raise serializers.ValidationError(_("Непідтримуваний сервіс push-сповіщень."))
        return value

    def validate_p256dh(self, value):
        try:
            return webpush.b64u_encode(webpush.load_public_point(value))
        except Exception:
            raise serializers.ValidationError(_("Невірний ключ підписки.")) from None

    def validate_auth(self, value):
        try:
            raw = webpush.b64u_decode(value)
        except Exception:
            raw = b""
        if len(raw) != 16:
            raise serializers.ValidationError(_("Невірний ключ підписки."))
        return webpush.b64u_encode(raw)


class VapidKeyView(APIView):
    def get(self, request):
        key = vapid_public_key()
        return Response({"public_key": key, "enabled": bool(key)})


class SubscriptionsView(APIView):
    def get(self, request):
        subs = PushSubscription.objects.filter(user=request.user)
        return Response({"count": subs.count(), "results": SubscriptionSerializer(subs, many=True).data})

    def post(self, request):
        """Створює або оновлює підписку цього браузера (за endpoint), разом із налаштуваннями подій."""
        if not vapid_public_key():
            raise ValidationError({"detail": _("Push-сповіщення не налаштовано на сервері.")})
        ser = SubscribeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)
        endpoint = data.pop("endpoint")
        replaces = data.pop("replaces", None)
        data["user_agent"] = (request.META.get("HTTP_USER_AGENT") or "")[:120]
        with transaction.atomic():
            sub = PushSubscription.objects.select_for_update().filter(endpoint=endpoint).first()
            created = sub is None or sub.user_id != request.user.pk
            if sub is not None and created:
                # Браузер тепер належить іншому акаунту — стара прив'язка більше не діє
                sub.delete()
            if created:
                sub = PushSubscription(user=request.user, endpoint=endpoint)
                old = PushSubscription.objects.filter(user=request.user, endpoint=replaces).first() if replaces else None
                if old is not None:
                    for field in PREF_FIELDS:
                        setattr(sub, field, getattr(old, field))
                    old.delete()
            for field, value in data.items():
                setattr(sub, field, value)
            sub.save()
            if created:
                # Не більше MAX_SUBSCRIPTIONS_PER_USER пристроїв: найстаріші видаляються
                ids = list(
                    PushSubscription.objects.filter(user=request.user)
                    .order_by("-created_at")
                    .values_list("pk", flat=True)[MAX_SUBSCRIPTIONS_PER_USER:]
                )
                PushSubscription.objects.filter(pk__in=ids).delete()
        return Response(SubscriptionSerializer(sub).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def delete(self, request):
        endpoint = request.data.get("endpoint") if isinstance(request.data, dict) else None
        if not isinstance(endpoint, str) or not endpoint:
            raise ValidationError({"endpoint": [_("Обов'язкове поле.")]})
        PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PushTestThrottle(UserRateThrottle):
    scope = "push_test"
    rate = "10/hour"


class TestPushView(APIView):
    throttle_classes = [PushTestThrottle]

    def post(self, request):
        sent = send_to_user(
            request.user, "test", _("Тестове сповіщення"), _("Push-сповіщення працюють на цьому пристрої."),
            "/settings", "test",
        )
        if not sent:
            raise ValidationError({"detail": _("Немає пристроїв, підписаних на сповіщення.")})
        return Response({"sent": sent})
