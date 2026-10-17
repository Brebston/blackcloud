"""API: обране, теги, «Нещодавні» (див. organize.py)."""

from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import organize
from .models import Tag
from .serializers import FileSerializer, FolderSerializer

MAX_TAGS = 100
MAX_BATCH = 500


def _items_response(user, folders, files, **extra) -> Response:
    data = {
        "folders": FolderSerializer(folders, many=True).data,
        "files": FileSerializer(files, many=True).data,
        **extra,
    }
    return Response(organize.decorate(user, data))


class StarView(APIView):
    """POST {file|folder, starred} — поставити чи зняти зірочку."""

    def post(self, request):
        starred = bool(request.data.get("starred", True))
        if request.data.get("file"):
            organize.set_star(request.user, file=organize.readable_file(request.user, request.data["file"]), starred=starred)
        elif request.data.get("folder"):
            organize.set_star(request.user, folder=organize.readable_folder(request.user, request.data["folder"]), starred=starred)
        else:
            raise ValidationError({"detail": _("Нічого не вибрано.")})
        return Response({"starred": starred})


class StarredView(APIView):
    def get(self, request):
        folders, files = organize.starred_items(request.user)
        return _items_response(request.user, folders, files)


class RecentView(APIView):
    def get(self, request):
        try:
            limit = max(1, min(int(request.query_params.get("limit") or 60), organize.RECENT_MAX))
        except ValueError:
            limit = 60
        rows = organize.recent_files(request.user, limit)
        files = FileSerializer([f for f, _w, _r in rows], many=True).data
        for item, (_f, when, reason) in zip(files, rows, strict=True):
            item["recent_at"] = when
            item["recent_reason"] = reason
        return Response(organize.decorate(request.user, {"folders": [], "files": files}))


class TagSerializer(serializers.ModelSerializer):
    count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Tag
        fields = ["id", "name", "color", "count", "created_at"]
        read_only_fields = ["id", "count", "created_at"]

    def validate_name(self, value):
        value = " ".join(value.split())
        if not value:
            raise serializers.ValidationError(_("Вкажіть назву тегу."))
        return value

    def validate_color(self, value):
        if value not in Tag.COLORS:
            raise serializers.ValidationError(_("Невідомий колір."))
        return value


def _tag(request, pk) -> Tag:
    tag = Tag.objects.filter(pk=pk, owner=request.user).first()
    if tag is None:
        raise NotFound()
    return tag


class TagsView(APIView):
    def get(self, request):
        from django.db.models import Count

        tags = Tag.objects.filter(owner=request.user).annotate(count=Count("items"))
        return Response(TagSerializer(tags, many=True).data)

    def post(self, request):
        if Tag.objects.filter(owner=request.user).count() >= MAX_TAGS:
            raise ValidationError({"detail": _("Забагато тегів (максимум %(n)s).") % {"n": MAX_TAGS}})
        ser = TagSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if Tag.objects.filter(owner=request.user, name__iexact=ser.validated_data["name"]).exists():
            raise ValidationError({"name": [_("Такий тег уже є.")]})
        try:
            with transaction.atomic():
                tag = ser.save(owner=request.user)
        except IntegrityError:
            raise ValidationError({"name": [_("Такий тег уже є.")]})
        return Response(TagSerializer(tag).data, status=201)


class TagDetailView(APIView):
    def patch(self, request, pk):
        tag = _tag(request, pk)
        ser = TagSerializer(tag, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        name = ser.validated_data.get("name")
        if name and Tag.objects.filter(owner=request.user, name__iexact=name).exclude(pk=tag.pk).exists():
            raise ValidationError({"name": [_("Такий тег уже є.")]})
        try:
            with transaction.atomic():
                tag = ser.save()
        except IntegrityError:
            raise ValidationError({"name": [_("Такий тег уже є.")]})
        return Response(TagSerializer(tag).data)

    def delete(self, request, pk):
        _tag(request, pk).delete()  # файли лишаються, зникає лише мітка
        return Response(status=204)


class TagItemsView(APIView):
    def get(self, request, pk):
        tag = _tag(request, pk)
        folders, files = organize.tagged_items(request.user, tag)
        return _items_response(request.user, folders, files, tag=TagSerializer(tag).data)


class TagAssignView(APIView):
    """POST {tag, files:[], folders:[], assign: true|false} — позначити чи зняти тег."""

    def post(self, request):
        tag = _tag(request, request.data.get("tag"))
        file_ids = request.data.get("files") or []
        folder_ids = request.data.get("folders") or []
        if not isinstance(file_ids, list) or not isinstance(folder_ids, list) or not (file_ids or folder_ids):
            raise ValidationError({"detail": _("Нічого не вибрано.")})
        if len(file_ids) + len(folder_ids) > MAX_BATCH:
            raise ValidationError({"detail": _("Забагато елементів (максимум %(n)s).") % {"n": MAX_BATCH}})
        files = [organize.readable_file(request.user, pk) for pk in dict.fromkeys(map(str, file_ids))]
        folders = [organize.readable_folder(request.user, pk) for pk in dict.fromkeys(map(str, folder_ids))]
        changed = organize.assign_tag(tag, files=files, folders=folders, assign=bool(request.data.get("assign", True)))
        return Response({"changed": changed})
