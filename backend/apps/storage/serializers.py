from django.utils.translation import gettext as _
from rest_framework import serializers

from .models import File, Folder, PublicLink, Share


class FolderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Folder
        fields = ["id", "name", "parent", "created_at", "updated_at", "deleted_at"]
        read_only_fields = ["id", "created_at", "updated_at", "deleted_at"]


class FileSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    downloadable = serializers.BooleanField(source="is_downloadable", read_only=True)
    owner = serializers.CharField(source="owner.username", read_only=True)
    shared = serializers.SerializerMethodField()

    class Meta:
        model = File
        fields = [
            "id",
            "name",
            "folder",
            "size",
            "mime_type",
            "sha256",
            "status",
            "status_display",
            "scan_detail",
            "downloadable",
            "has_thumbnail",
            "has_preview",
            "content_version",
            "owner",
            "shared",
            "created_at",
            "updated_at",
            "deleted_at",
        ]
        read_only_fields = fields

    def get_shared(self, obj):
        ann = getattr(obj, "share_count", None)
        return bool(ann) if ann is not None else None


class StartUploadSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=1024)
    size = serializers.IntegerField(min_value=0)
    folder = serializers.UUIDField(required=False, allow_null=True)
    # Явно: зберегти як нову версію цього файлу (name/folder тоді ігноруються)
    replace_file_id = serializers.UUIDField(required=False, allow_null=True)


class RenameMoveSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=1024, required=False)
    # "root" або UUID; відсутність поля = не переміщувати
    target = serializers.CharField(max_length=64, required=False, allow_null=True)


class CreateFolderSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=1024)
    parent = serializers.UUIDField(required=False, allow_null=True)


class ShareSerializer(serializers.ModelSerializer):
    recipient = serializers.CharField(source="recipient.username", read_only=True)
    owner = serializers.CharField(source="owner.username", read_only=True)
    target_name = serializers.SerializerMethodField()
    target_type = serializers.SerializerMethodField()

    class Meta:
        model = Share
        fields = ["id", "owner", "recipient", "file", "folder", "target_name", "target_type", "created_at"]

    def get_target_name(self, obj):
        return obj.file.name if obj.file_id else obj.folder.name

    def get_target_type(self, obj):
        return "file" if obj.file_id else "folder"


class CreateShareSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=40)
    file = serializers.UUIDField(required=False, allow_null=True)
    folder = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        if bool(attrs.get("file")) == bool(attrs.get("folder")):
            raise serializers.ValidationError(_("Вкажіть або file, або folder."))
        return attrs


class PublicLinkSerializer(serializers.ModelSerializer):
    file_name = serializers.CharField(source="target_name", read_only=True)
    has_password = serializers.SerializerMethodField()
    active = serializers.BooleanField(source="is_active", read_only=True)

    class Meta:
        model = PublicLink
        fields = [
            "id",
            "kind",
            "file",
            "folder",
            "file_name",
            "note",
            "expires_at",
            "max_downloads",
            "download_count",
            "upload_count",
            "upload_bytes",
            "max_upload_bytes",
            "has_password",
            "active",
            "created_at",
        ]

    def get_has_password(self, obj):
        return bool(obj.password_hash)


class CreatePublicLinkSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["file", "folder", "drop"], default="file")
    file = serializers.UUIDField(required=False, allow_null=True)
    folder = serializers.UUIDField(required=False, allow_null=True)
    note = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    # Скринька: ліміт на всі надіслані файли разом (МБ); без нього — лише квота власника
    max_upload_mb = serializers.IntegerField(min_value=1, max_value=10_000_000, required=False, allow_null=True)
    expires_days = serializers.IntegerField(min_value=1, max_value=365, default=7)
    password = serializers.CharField(max_length=200, required=False, allow_blank=True, trim_whitespace=False)
    max_downloads = serializers.IntegerField(min_value=1, max_value=100000, required=False, allow_null=True)

    def validate_password(self, value):
        from django.conf import settings

        if value and len(value) < settings.PUBLIC_LINK_MIN_PASSWORD:
            raise serializers.ValidationError(
                _("Пароль посилання — щонайменше %(n)s символів.") % {"n": settings.PUBLIC_LINK_MIN_PASSWORD}
            )
        return value

    def validate(self, attrs):
        if attrs["kind"] == "file" and not attrs.get("file"):
            raise serializers.ValidationError({"file": [_("Оберіть файл.")]})
        if attrs["kind"] != "file" and not attrs.get("folder"):
            raise serializers.ValidationError({"folder": [_("Оберіть теку.")]})
        return attrs
