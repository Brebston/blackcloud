"""WebDAV (RFC 4918, класи 1 і 2) для файлів користувача: /dav/<шлях>.

Підключення: macOS Finder («Підключитися до сервера» → https://<домен>/dav/),
Windows («Підключити мережевий диск»), davfs2/GNOME, Cyberduck тощо.

Безпека:
* автентифікація лише HTTP Basic з паролем застосунку WebDAV (auth.py), лише HTTPS;
  сесійні cookie не використовуються, тож CSRF неможливий; запити, які браузер
  позначає як міжсайтові (Sec-Fetch-Site / Origin), відхиляються;
* доступ лише до власного дерева (не до спільних і не до кошика);
* вміст віддається лише для файлів, перевірених антивірусом, і завжди як
  application/octet-stream + attachment + CSP sandbox (як і звичайне завантаження);
* XML-тіла розбираються без DTD/сутностей (xmlutil.py).
"""

import logging
import tempfile
import urllib.parse
from datetime import timezone as dt_timezone

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponse, StreamingHttpResponse
from django.utils.http import http_date
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.storage import crypto, services
from apps.storage.models import File, FileVersion, Folder

from . import auth, locks, ops, paths, xmlutil
from .xmlutil import dav

logger = logging.getLogger("blackcloud")
User = get_user_model()

ALLOW = "OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, PROPPATCH, MKCOL, COPY, MOVE, LOCK, UNLOCK"
METHODS = set(ALLOW.replace(" ", "").split(","))
XML_CT = 'application/xml; charset="utf-8"'

LIVE_PROPS = [
    dav("displayname"),
    dav("resourcetype"),
    dav("getlastmodified"),
    dav("creationdate"),
    dav("getcontentlength"),
    dav("getcontenttype"),
    dav("getetag"),
    dav("supportedlock"),
    dav("lockdiscovery"),
]
QUOTA_PROPS = [dav("quota-available-bytes"), dav("quota-used-bytes")]


class DavError(Exception):
    def __init__(self, status: int, message: str = "", condition: str | None = None, headers: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.condition = condition
        self.headers = headers or {}


# ─────────────────────────── Відповіді ───────────────────────────


def _base_headers(response) -> HttpResponse:
    response["DAV"] = "1, 2"
    response["MS-Author-Via"] = "DAV"
    response["X-Content-Type-Options"] = "nosniff"
    if "Cache-Control" not in response:
        response["Cache-Control"] = "no-store"
    return response


def _plain(status: int, message: str = "", headers: dict | None = None) -> HttpResponse:
    response = HttpResponse(message + ("\n" if message else ""), status=status, content_type="text/plain; charset=utf-8")
    for k, v in (headers or {}).items():
        response[k] = v
    return _base_headers(response)


def _error(exc: DavError) -> HttpResponse:
    if exc.condition:
        response = HttpResponse(xmlutil.error_body(exc.condition), status=exc.status, content_type=XML_CT)
        for k, v in exc.headers.items():
            response[k] = v
        return _base_headers(response)
    return _plain(exc.status, exc.message, exc.headers)


def _unauthorized(message: str = "") -> HttpResponse:
    return _plain(
        401,
        message or _("Потрібен пароль застосунку WebDAV (див. Налаштування → WebDAV)."),
        {"WWW-Authenticate": 'Basic realm="BlackCloud", charset="UTF-8"'},
    )


def _multistatus(responses: list[str]) -> HttpResponse:
    return _base_headers(HttpResponse(xmlutil.multistatus(responses), status=207, content_type=XML_CT))


def _detail(exc) -> str:
    detail = getattr(exc, "detail", "")
    if isinstance(detail, dict):
        for value in detail.values():
            if isinstance(value, list) and value:
                return str(value[0])
            if value:
                return str(value)
    if isinstance(detail, list) and detail:
        return str(detail[0])
    return str(detail)


# ─────────────────────────── Вхід ───────────────────────────


def _cross_site(request) -> bool:
    """WebDAV-клієнти не надсилають Sec-Fetch-* і Origin; браузер на чужому сайті — так."""
    site = request.META.get("HTTP_SEC_FETCH_SITE", "").lower()
    if site in ("cross-site", "same-site"):
        return True
    origin = request.META.get("HTTP_ORIGIN")
    if origin and origin != "null":
        parts = urllib.parse.urlsplit(origin)
        if parts.netloc.lower() != request.get_host().lower():
            return True
    elif origin == "null":
        return True
    return False


@csrf_exempt
def dav_view(request, path: str = ""):
    try:
        return _dispatch(request, path or "")
    except DavError as exc:
        return _error(exc)
    except paths.BadPath:
        return _plain(400, _("Невірний шлях."))
    except xmlutil.XMLRejected:
        return _plain(400, _("Невірне або заборонене XML-тіло запиту."))
    except NotFound as exc:
        return _plain(404, _detail(exc))
    except ValidationError as exc:
        return _plain(400, _detail(exc))
    except PermissionDenied as exc:
        return _plain(403, _detail(exc))


def _dispatch(request, path: str) -> HttpResponse:
    method = request.method.upper()
    if method not in METHODS:
        return _plain(405, headers={"Allow": ALLOW})
    if _cross_site(request):
        return _plain(403, _("Міжсайтові запити до WebDAV заборонені."))
    if not request.is_secure() and not (settings.DEBUG or getattr(settings, "TESTING", False)):
        return _plain(403, _("WebDAV доступний лише через HTTPS."))
    paths.check_raw(paths.raw_path(request))
    segments = paths.split(path)

    if method == "OPTIONS":
        # Без автентифікації: відповідь не розкриває нічого про ресурси (Windows і Office
        # надсилають OPTIONS анонімно перед автентифікацією)
        return _plain(200, headers={"Allow": ALLOW, "Accept-Ranges": "bytes"})

    result = auth.authenticate(request)
    if result.locked:
        return _unauthorized(_("Забагато невдалих спроб. Спробуйте пізніше."))
    if result.user is None:
        return _unauthorized()
    user = result.user
    handler = HANDLERS[method]
    return handler(request, user, segments)


# ─────────────────────────── Блокування запису ───────────────────────────


def _require_unlocked(request, user, node: paths.Node, *, subtree=False, membership=True):
    parent = paths.key(node.parent_segments) if (membership and node.segments) else None
    if not locks.check_write(request, user.pk, node.key, subtree=subtree, parent=parent):
        raise DavError(423, condition="lock-token-submitted")


def _check_name(segments) -> str:
    name = segments[-1]
    cleaned = services.clean_name(name)
    if cleaned != name:
        raise ValidationError({"name": [_("Невірне ім'я.")]})
    return name


# ─────────────────────────── PROPFIND / PROPPATCH ───────────────────────────


def _read_xml(request):
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > xmlutil.MAX_XML_BYTES:
        raise xmlutil.XMLRejected("too large")
    data = request.read(xmlutil.MAX_XML_BYTES + 1)
    return xmlutil.parse(data)


def _iso(dt) -> str:
    return dt.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_mtime(f: File):
    return getattr(f, "content_updated_at", None) or f.created_at


def etag(f: File) -> str:
    return f'"{f.id.hex}-{f.content_version}-{f.size}"'


def _props_for(node: paths.Node, user, all_locks: list[dict]) -> dict[str, str | None]:
    """Значення живих властивостей (внутрішній XML). None — властивості немає (404)."""
    props: dict[str, str | None] = {}
    if node.is_root:
        props[dav("displayname")] = xmlutil.text(user.username)
        props[dav("resourcetype")] = "<D:collection/>"
        props[dav("creationdate")] = _iso(user.date_joined)
        props[dav("getlastmodified")] = xmlutil.text(http_date(user.date_joined.timestamp()))
    elif node.folder is not None:
        fo = node.folder
        props[dav("displayname")] = xmlutil.text(fo.name)
        props[dav("resourcetype")] = "<D:collection/>"
        props[dav("creationdate")] = _iso(fo.created_at)
        props[dav("getlastmodified")] = xmlutil.text(http_date(fo.updated_at.timestamp()))
        props[dav("getetag")] = xmlutil.text(f'"{fo.id.hex}-{int(fo.updated_at.timestamp())}"')
    else:
        f = node.file
        props[dav("displayname")] = xmlutil.text(f.name)
        props[dav("resourcetype")] = ""
        props[dav("creationdate")] = _iso(f.created_at)
        props[dav("getlastmodified")] = xmlutil.text(http_date(_file_mtime(f).timestamp()))
        props[dav("getcontentlength")] = str(f.size)
        props[dav("getcontenttype")] = xmlutil.text(f.mime_type or "application/octet-stream")
        props[dav("getetag")] = xmlutil.text(etag(f))
    props[dav("supportedlock")] = locks.SUPPORTED_LOCK
    active = [lk for lk in all_locks if locks._covers(lk, node.key)]
    props[dav("lockdiscovery")] = "".join(
        locks.activelock_xml(lk, paths.href(tuple(lk["path"].split("/")) if lk["path"] else (), False))
        for lk in active
    )
    if node.is_collection:
        used = user.used_bytes
        props[dav("quota-used-bytes")] = str(used)
        props[dav("quota-available-bytes")] = str(max(0, user.quota_bytes - used))
    return props


def _propfind_response(node: paths.Node, user, all_locks, mode: str, wanted: list[str]) -> str:
    values = _props_for(node, user, all_locks)
    if mode == "propname":
        names = [xmlutil.prop_element(tag) for tag in values]
        return xmlutil.response(node.href, [xmlutil.propstat(names, 200)])
    if mode == "allprop":
        tags = [t for t in LIVE_PROPS if values.get(t) is not None] + [t for t in wanted if t not in LIVE_PROPS]
    else:
        tags = wanted
    found, missing = [], []
    for tag in tags:
        value = values.get(tag)
        if value is None:
            missing.append(xmlutil.prop_element(tag))
        else:
            found.append(xmlutil.prop_element(tag, value))
    stats = []
    if found:
        stats.append(xmlutil.propstat(found, 200))
    if missing:
        stats.append(xmlutil.propstat(missing, 404))
    if not stats:
        stats.append(xmlutil.propstat([], 200))
    return xmlutil.response(node.href, stats)


def propfind(request, user, segments):
    depth = request.META.get("HTTP_DEPTH", "infinity").replace(" ", "").lower()
    noroot = depth == "1,noroot"  # розширення Microsoft: лише вміст колекції
    if noroot:
        depth = "1"
    if depth not in ("0", "1"):
        raise DavError(403, condition="propfind-finite-depth")
    root = _read_xml(request)
    mode, wanted = "allprop", []
    if root is not None:
        if root.tag != dav("propfind"):
            raise xmlutil.XMLRejected("propfind expected")
        for child in root:
            if child.tag == dav("prop"):
                mode, wanted = "prop", [p.tag for p in child]
            elif child.tag == dav("propname"):
                mode = "propname"
            elif child.tag == dav("allprop"):
                mode = "allprop"
            elif child.tag == dav("include"):
                wanted += [p.tag for p in child]
    node = paths.resolve(user, segments)
    if not node.exists:
        raise DavError(404, _("Не знайдено."))
    user = User.objects.get(pk=user.pk)  # актуальна квота
    all_locks = list(locks._load(user.pk).values())
    responses = [] if (noroot and node.is_collection) else [_propfind_response(node, user, all_locks, mode, wanted)]
    if depth == "1" and node.is_collection:
        parent = node.folder
        for fo in Folder.objects.filter(owner=user, parent=parent, deleted_at__isnull=True).order_by("name"):
            child = paths.Node(node.segments + (fo.name,), folder=fo, parent=parent)
            responses.append(_propfind_response(child, user, all_locks, mode, wanted))
        for f in paths.visible_files(File.objects.filter(owner=user, folder=parent)).order_by("name"):
            child = paths.Node(node.segments + (f.name,), file=f, parent=parent)
            responses.append(_propfind_response(child, user, all_locks, mode, wanted))
    return _multistatus(responses)


def proppatch(request, user, segments):
    """Мертві властивості не зберігаються: Finder/Windows записують свої службові
    властивості (час, атрибути) і лише перевіряють, що відповідь — 200 для кожної."""
    node = paths.resolve(user, segments)
    if not node.exists:
        raise DavError(404, _("Не знайдено."))
    _require_unlocked(request, user, node, membership=False)
    root = _read_xml(request)
    if root is None or root.tag != dav("propertyupdate"):
        raise xmlutil.XMLRejected("propertyupdate expected")
    names = []
    for action in root:
        if action.tag not in (dav("set"), dav("remove")):
            continue
        for prop in action:
            if prop.tag == dav("prop"):
                names += [xmlutil.prop_element(p.tag) for p in prop]
    return _multistatus([xmlutil.response(node.href, [xmlutil.propstat(names, 200)])])


# ─────────────────────────── GET / HEAD ───────────────────────────


def _parse_range(header: str, size: int):
    """Один діапазон bytes=a-b / a- / -n. None — ігнорувати; "invalid" — 416."""
    if not header or not header.startswith("bytes=") or "," in header or size == 0:
        return None
    spec = header[6:].strip()
    start_s, sep, end_s = spec.partition("-")
    if not sep:
        return None
    try:
        if start_s == "":
            n = int(end_s)
            if n <= 0:
                return "invalid"
            return max(0, size - n), size - 1
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return "invalid"
    return start, min(end, size - 1)


async def _aiter_range(f: File, start: int, end: int):
    data_key = crypto.unwrap_key(f.wrapped_key, f.key_version, f.id)
    cs = f.chunk_size
    for index in range(start // cs, end // cs + 1):
        chunk = await sync_to_async(services.read_chunk, thread_sensitive=False)(f, data_key, index)
        base = index * cs
        yield chunk[max(start - base, 0) : min(end - base + 1, len(chunk))]


def _denied_message(f: File) -> str:
    if f.status == File.Status.SCANNING:
        return _("Файл ще перевіряється антивірусом. Спробуйте за хвилину.")
    if f.status == File.Status.INFECTED:
        return _("Файл заблоковано антивірусом.")
    return _("Файл недоступний для завантаження (статус: %(status)s).") % {"status": f.get_status_display()}


def get(request, user, segments, head=False):
    node = paths.resolve(user, segments)
    if not node.exists:
        raise DavError(404, _("Не знайдено."))
    if node.is_collection:
        folders = Folder.objects.filter(owner=user, parent=node.folder, deleted_at__isnull=True).order_by("name")
        files = paths.visible_files(File.objects.filter(owner=user, folder=node.folder)).order_by("name")
        listing = "\n".join([f"{fo.name}/" for fo in folders] + [f.name for f in files])
        response = _plain(200, "" if head else listing)
        return response
    f = node.file
    if not f.is_downloadable:
        raise DavError(403, _denied_message(f))
    size = f.size
    rng = _parse_range(request.META.get("HTTP_RANGE", ""), size)
    if rng == "invalid":
        raise DavError(416, headers={"Content-Range": f"bytes */{size}"})
    if head:
        response = HttpResponse(b"", content_type="application/octet-stream")
        length = size
    elif rng is None:
        response = StreamingHttpResponse(services.aiter_plaintext(f), content_type="application/octet-stream")
        length = size
    else:
        start, end = rng
        response = StreamingHttpResponse(_aiter_range(f, start, end), content_type="application/octet-stream", status=206)
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
        length = end - start + 1
    response["Content-Length"] = str(length)
    response["Accept-Ranges"] = "bytes"
    response["ETag"] = etag(f)
    response["Last-Modified"] = http_date(_file_mtime(f).timestamp())
    response["Content-Disposition"] = services.content_disposition("attachment", f.name)
    response["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; sandbox"
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "private, no-store"
    return _base_headers(response)


def head(request, user, segments):
    return get(request, user, segments, head=True)


# ─────────────────────────── PUT ───────────────────────────


def _body(request):
    """(потік, розмір) тіла запиту без читання його в пам'ять."""
    raw = request.META.get("CONTENT_LENGTH")
    if raw not in (None, ""):
        try:
            size = int(raw)
        except ValueError:
            raise DavError(400, _("Невірний Content-Length."))
        return request, size
    # Chunked (Finder): під ASGI тіло вже лежить у тимчасовому файлі — дізнаємось розмір
    stream = getattr(request, "_stream", None)
    if stream is not None and hasattr(stream, "seek") and hasattr(stream, "tell"):
        try:
            pos = stream.tell()
            end = stream.seek(0, 2)
            stream.seek(pos)
            return request, end - pos
        except (OSError, ValueError):
            pass
    spool = tempfile.TemporaryFile()
    size = 0
    while True:
        piece = request.read(ops.READ_PIECE)
        if not piece:
            break
        size += len(piece)
        if size > settings.MAX_FILE_SIZE:
            spool.close()
            raise DavError(413, _("Файл завеликий."))
        spool.write(piece)
    spool.seek(0)
    return spool, size


def put(request, user, segments):
    if not segments:
        raise DavError(405, _("Не можна записати файл у корінь як ресурс."), headers={"Allow": ALLOW})
    node = paths.resolve(user, segments)
    if node.parent_missing:
        raise DavError(409, _("Батьківської папки не існує."))
    if node.is_collection:
        raise DavError(405, _("Це папка."), headers={"Allow": ALLOW})
    name = _check_name(segments)
    _require_unlocked(request, user, node, membership=node.file is None)
    stream, size = _body(request)
    if size < 0:
        raise DavError(400, _("Невірний Content-Length."))
    if size > settings.MAX_FILE_SIZE:
        raise DavError(
            413, _("Максимальний розмір файлу — %(n)s ГБ.") % {"n": settings.MAX_FILE_SIZE // 1024**3}
        )
    try:
        if node.file is not None:
            previous_size, previous_version = node.file.size, node.file.content_version
            f = ops.overwrite_file(request, user, node.file, stream, size)
            if previous_size == 0:
                _drop_empty_version(f, previous_version)
            status = 204
        else:
            f = ops.create_file(request, user, node.parent, name, stream, size)
            status = 201
    except ops.BodyMismatch:
        raise DavError(400, _("Розмір тіла запиту не збігається з Content-Length."))
    except ops.Conflict:
        raise DavError(409, _("Файл з таким ім'ям щойно створено іншим клієнтом."))
    except PermissionDenied as exc:
        raise DavError(507, _detail(exc))
    response = _plain(status)
    response["ETag"] = etag(f)
    return response


def _drop_empty_version(f: File, number: int) -> None:
    """Finder і Windows спершу створюють порожній файл, а потім записують вміст.
    Порожню «версію» в історії не зберігаємо."""
    try:
        v = FileVersion.objects.filter(file=f, number=number, size=0).first()
        if v is not None:
            services.delete_version(f, v)
    except Exception:
        logger.warning("failed to drop empty version of %s", f.pk, exc_info=True)


# ─────────────────────────── DELETE / MKCOL ───────────────────────────


def delete(request, user, segments):
    if not segments:
        raise DavError(403, _("Корінь не можна видалити."))
    node = paths.resolve(user, segments)
    if not node.exists:
        raise DavError(404, _("Не знайдено."))
    _require_unlocked(request, user, node, subtree=node.is_collection)
    ops.trash(request, user, node)
    locks.drop_subtree(user.pk, node.key)
    return _plain(204)


def mkcol(request, user, segments):
    if request.read(1):
        raise DavError(415, _("MKCOL з тілом не підтримується."))
    if not segments:
        raise DavError(405, headers={"Allow": ALLOW})
    node = paths.resolve(user, segments)
    if node.parent_missing:
        raise DavError(409, _("Батьківської папки не існує."))
    if node.exists:
        raise DavError(405, _("Ресурс уже існує."), headers={"Allow": ALLOW})
    name = _check_name(segments)
    _require_unlocked(request, user, node)
    try:
        ops.make_folder(request, user, node.parent, name)
    except ops.Conflict:
        raise DavError(405, _("Ресурс уже існує."), headers={"Allow": ALLOW})
    return _plain(201)


# ─────────────────────────── COPY / MOVE ───────────────────────────


def _destination(request) -> tuple[str, ...]:
    value = request.META.get("HTTP_DESTINATION", "")
    if not value:
        raise DavError(400, _("Потрібен заголовок Destination."))
    try:
        return paths.parse_destination(request, value)
    except paths.BadPath as exc:
        if str(exc) in ("other host", "outside /dav/", "bad scheme"):
            raise DavError(502, _("Destination має вказувати на цей сервер (/dav/)."))
        raise


def copy_move(request, user, segments, *, is_move: bool):
    if not segments:
        raise DavError(403, _("Корінь не можна копіювати чи переміщувати."))
    src = paths.resolve(user, segments)
    if not src.exists:
        raise DavError(404, _("Не знайдено."))
    dst_segments = _destination(request)
    if not dst_segments:
        raise DavError(403, _("Не можна замінити корінь."))
    if dst_segments == segments:
        raise DavError(403, _("Джерело й призначення збігаються."))
    if src.is_collection and dst_segments[: len(segments)] == segments:
        raise DavError(403, _("Не можна скопіювати чи перемістити папку саму в себе."))
    overwrite = request.META.get("HTTP_OVERWRITE", "T").strip().upper() != "F"
    dst = paths.resolve(user, dst_segments)
    if dst.parent_missing:
        raise DavError(409, _("Батьківської папки призначення не існує."))
    name = _check_name(dst_segments)
    same_object = (dst.file is not None and src.file is not None and dst.file.pk == src.file.pk) or (
        dst.folder is not None and src.folder is not None and dst.folder.pk == src.folder.pk
    )
    existed = dst.exists and not same_object
    if existed and not overwrite:
        raise DavError(412, _("Призначення вже існує (Overwrite: F)."))

    if is_move:
        _require_unlocked(request, user, src, subtree=src.is_collection)
    _require_unlocked(request, user, dst, subtree=dst.is_collection)

    if is_move:
        with transaction.atomic():
            if existed:
                ops.trash(request, user, dst)
            try:
                ops.move(request, user, src, dst.parent, name)
            except ops.Conflict:
                raise DavError(412, _("Призначення вже існує."))
        locks.drop_subtree(user.pk, src.key)
        if existed:
            locks.drop_subtree(user.pk, dst.key)
        return _plain(204 if existed else 201)

    # COPY
    if src.file is not None and not src.file.is_downloadable:
        raise DavError(403, _denied_message(src.file))
    if src.file is not None:
        # Квоту перевіряємо до того, як прибрати наявне призначення в кошик
        fresh = User.objects.get(pk=user.pk)
        if fresh.quota_bytes - fresh.used_bytes < src.file.size:
            raise DavError(507, _("Недостатньо місця у сховищі."))
    if existed:
        ops.trash(request, user, dst)
        locks.drop_subtree(user.pk, dst.key)
    try:
        if src.file is not None:
            ops.copy_file(request, user, src.file, dst.parent, name)
        elif request.META.get("HTTP_DEPTH", "infinity").strip() == "0":
            ops.make_folder(request, user, dst.parent, name)
        else:
            ops.copy_folder(request, user, src.folder, dst.parent, name)
    except ops.Conflict:
        raise DavError(412, _("Призначення вже існує."))
    except ops.CopyTooLarge:
        raise DavError(
            403, _("Папка завелика для копіювання на сервері. Скопіюйте її частинами або через вебінтерфейс.")
        )
    except PermissionError:
        raise DavError(403, _("У папці є файли, які ще не перевірено антивірусом або заблоковано."))
    except PermissionDenied as exc:
        raise DavError(507, _detail(exc))
    return _plain(204 if existed else 201)


def copy(request, user, segments):
    return copy_move(request, user, segments, is_move=False)


def move(request, user, segments):
    return copy_move(request, user, segments, is_move=True)


# ─────────────────────────── LOCK / UNLOCK ───────────────────────────


def _lock_response(lock: dict, status: int, *, new: bool) -> HttpResponse:
    root_href = paths.href(tuple(lock["path"].split("/")) if lock["path"] else (), False)
    body = (
        '<?xml version="1.0" encoding="utf-8"?>\n<D:prop xmlns:D="DAV:"><D:lockdiscovery>'
        + locks.activelock_xml(lock, root_href)
        + "</D:lockdiscovery></D:prop>"
    ).encode()
    response = HttpResponse(body, status=status, content_type=XML_CT)
    if new:
        response["Lock-Token"] = f"<{lock['token']}>"
    return _base_headers(response)


def lock(request, user, segments):
    timeout = locks.parse_timeout(request.META.get("HTTP_TIMEOUT"))
    root = _read_xml(request)
    node = paths.resolve(user, segments)
    key = node.key

    if root is None:
        # Оновлення наявного блокування (токен у заголовку If)
        if not node.exists:
            raise DavError(404, _("Не знайдено."))
        lk = locks.refresh(user.pk, key, locks.submitted_tokens(request), timeout)
        if lk is None:
            raise DavError(412, condition="lock-token-matches-request-uri")
        return _lock_response(lk, 200, new=False)

    if root.tag != dav("lockinfo"):
        raise xmlutil.XMLRejected("lockinfo expected")
    scope = "exclusive"
    owner = ""
    for child in root:
        if child.tag == dav("lockscope"):
            for s in child:
                if s.tag == dav("shared"):
                    scope = "shared"
        elif child.tag == dav("owner"):
            owner = " ".join("".join(child.itertext()).split())
    depth = request.META.get("HTTP_DEPTH", "infinity").strip().lower()
    if depth not in ("0", "infinity"):
        raise DavError(400, _("Depth для LOCK — 0 або infinity."))
    if not node.is_collection:
        depth = "0"
    if node.parent_missing:
        raise DavError(409, _("Батьківської папки не існує."))

    lk = locks.create(user.pk, key, scope=scope, depth=depth, owner=owner, timeout=timeout)
    if lk is None:
        raise DavError(423, condition="no-conflicting-lock")
    status = 200
    if not node.exists:
        # Блокування неіснуючого ресурсу створює порожній файл (RFC 4918, 7.3)
        try:
            name = _check_name(segments)
            if not locks.check_write(request, user.pk, key, parent=paths.key(node.parent_segments), resource=False):
                raise DavError(423, condition="lock-token-submitted")
            ops.create_file(request, user, node.parent, name, ops.IterStream([]), 0)
        except Exception:
            locks.release(user.pk, key, lk["token"])
            raise
        status = 201
    return _lock_response(lk, status, new=True)


def unlock(request, user, segments):
    tokens = locks.TOKEN_RE.findall(request.META.get("HTTP_LOCK_TOKEN", ""))
    if not tokens:
        raise DavError(400, _("Потрібен заголовок Lock-Token."))
    node = paths.resolve(user, segments)
    if not locks.release(user.pk, node.key, tokens[0]):
        raise DavError(409, condition="lock-token-matches-request-uri")
    return _plain(204)


HANDLERS = {
    "PROPFIND": propfind,
    "PROPPATCH": proppatch,
    "GET": get,
    "HEAD": head,
    "PUT": put,
    "DELETE": delete,
    "MKCOL": mkcol,
    "COPY": copy,
    "MOVE": move,
    "LOCK": lock,
    "UNLOCK": unlock,
}
