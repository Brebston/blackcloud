import zoneinfo

from django.contrib.auth import password_validation
from rest_framework import serializers

from .models import Invite, Preferences, User, UserSession


class UserSerializer(serializers.ModelSerializer):
    has_2fa = serializers.BooleanField(read_only=True)
    mailbox = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "display_name",
            "is_staff",
            "has_2fa",
            "quota_bytes",
            "used_bytes",
            "date_joined",
            "mailbox",
        ]
        read_only_fields = fields

    def get_mailbox(self, obj):
        mb = getattr(obj, "mailbox", None)
        return mb.address if mb else None


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["display_name"]


class PreferencesSerializer(serializers.ModelSerializer):
    class Meta:
        model = Preferences
        exclude = ["user"]

    def validate_timezone(self, value):
        if value not in zoneinfo.available_timezones():
            raise serializers.ValidationError("Невідомий часовий пояс.")
        return value


class LoginSerializer(serializers.Serializer):
    login = serializers.CharField(max_length=254)
    password = serializers.CharField(max_length=1024, trim_whitespace=False)


class TwoFactorSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=20)


class PasswordConfirmSerializer(serializers.Serializer):
    password = serializers.CharField(max_length=1024, trim_whitespace=False)


class DisableTwoFactorSerializer(PasswordConfirmSerializer):
    code = serializers.CharField(max_length=20)


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(max_length=1024, trim_whitespace=False)
    new_password = serializers.CharField(max_length=1024, trim_whitespace=False)

    def validate(self, attrs):
        password_validation.validate_password(attrs["new_password"], self.context["request"].user)
        return attrs


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=40)
    email = serializers.EmailField()
    password = serializers.CharField(max_length=1024, trim_whitespace=False)
    invite = serializers.CharField(max_length=200, required=False, allow_blank=True)

    def validate_username(self, value):
        value = value.lower()
        from .models import USERNAME_VALIDATOR

        USERNAME_VALIDATOR(value)
        reserved = {"admin", "root", "postmaster", "abuse", "webmaster", "hostmaster", "noreply", "mailer-daemon", "webmail"}
        if value in reserved:
            raise serializers.ValidationError("Це ім'я зарезервоване.")
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Це ім'я вже зайняте.")
        return value

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Цей email вже використовується.")
        return value

    def validate(self, attrs):
        tmp = User(username=attrs["username"], email=attrs["email"])
        password_validation.validate_password(attrs["password"], tmp)
        return attrs


class SessionSerializer(serializers.ModelSerializer):
    current = serializers.SerializerMethodField()

    class Meta:
        model = UserSession
        fields = ["session_key", "ip_address", "user_agent", "created_at", "last_seen", "current"]

    def get_current(self, obj):
        return obj.session_key == self.context["request"].session.session_key

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Повний ключ сесії не віддаємо — лише ідентифікатор для відкликання
        data["id"] = instance.session_key[:12]
        data.pop("session_key")
        return data


class AdminUserSerializer(serializers.ModelSerializer):
    has_2fa = serializers.BooleanField(read_only=True)
    quota_gb = serializers.FloatField(write_only=True, required=False, min_value=0, max_value=100000)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "display_name",
            "is_active",
            "is_staff",
            "has_2fa",
            "quota_bytes",
            "used_bytes",
            "quota_gb",
            "date_joined",
            "last_login",
        ]
        read_only_fields = ["id", "username", "email", "has_2fa", "used_bytes", "date_joined", "last_login", "quota_bytes"]

    def update(self, instance, validated_data):
        quota_gb = validated_data.pop("quota_gb", None)
        if quota_gb is not None:
            instance.quota_bytes = int(quota_gb * 1024**3)
        return super().update(instance, validated_data)


class AdminCreateUserSerializer(RegisterSerializer):
    invite = None
    quota_gb = serializers.FloatField(required=False, min_value=0, max_value=100000)
    is_staff = serializers.BooleanField(required=False, default=False)


class InviteSerializer(serializers.ModelSerializer):
    valid = serializers.BooleanField(source="is_valid", read_only=True)
    quota_gb = serializers.FloatField(write_only=True, required=False, min_value=0)
    days = serializers.IntegerField(write_only=True, required=False, min_value=1, max_value=30)

    class Meta:
        model = Invite
        fields = ["id", "email", "quota_bytes", "created_at", "expires_at", "used_at", "valid", "quota_gb", "days"]
        read_only_fields = ["id", "quota_bytes", "created_at", "expires_at", "used_at", "valid"]


class PublicUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "display_name"]
