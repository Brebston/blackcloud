"""Безпечний розбір XML-тіл WebDAV і побудова відповідей multistatus.

Розбір — напряму через expat (без xml.etree.XMLParser), щоб гарантовано
відхиляти DOCTYPE та будь-які оголошення сутностей (XXE, «billion laughs»).
Тіла запитів WebDAV маленькі: обмежуємо розмір, глибину й кількість елементів.
"""

from xml.etree import ElementTree as ET
from xml.parsers import expat
from xml.sax.saxutils import escape, quoteattr

DAV_NS = "DAV:"
MAX_XML_BYTES = 64 * 1024
MAX_DEPTH = 32
MAX_ELEMENTS = 2000


class XMLRejected(ValueError):
    pass


def _forbid(*_args):
    raise XMLRejected("DTD/entities are not allowed")


def parse(data: bytes) -> ET.Element | None:
    """Порожнє тіло → None. Помилка/заборонена конструкція → XMLRejected."""
    if not data or not data.strip():
        return None
    if len(data) > MAX_XML_BYTES:
        raise XMLRejected("body too large")
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.StartDoctypeDeclHandler = _forbid
    parser.EntityDeclHandler = _forbid
    parser.UnparsedEntityDeclHandler = _forbid
    parser.ExternalEntityRefHandler = _forbid
    parser.NotationDeclHandler = _forbid
    try:
        # Без цього expat підставляє лише 5 вбудованих сутностей; невідомі → помилка
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    except AttributeError:  # pragma: no cover
        pass

    stack: list[ET.Element] = []
    root: list[ET.Element] = []
    count = [0]

    def _tag(name: str) -> str:
        ns, sep, local = name.rpartition(" ")
        return f"{{{ns}}}{local}" if sep else local

    def start(name, attrs):
        count[0] += 1
        if count[0] > MAX_ELEMENTS or len(stack) >= MAX_DEPTH:
            raise XMLRejected("document too complex")
        el = ET.Element(_tag(name))
        if stack:
            stack[-1].append(el)
        else:
            root.append(el)
        stack.append(el)

    def end(_name):
        stack.pop()

    def chars(text):
        if not stack:
            return
        el = stack[-1]
        if len(el):
            last = el[-1]
            last.tail = (last.tail or "") + text
        else:
            el.text = (el.text or "") + text

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    try:
        parser.Parse(data, True)
    except XMLRejected:
        raise
    except expat.ExpatError as exc:
        raise XMLRejected(str(exc)) from exc
    if not root:
        raise XMLRejected("empty document")
    return root[0]


def dav(local: str) -> str:
    return f"{{{DAV_NS}}}{local}"


def split_tag(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        ns, _, local = tag[1:].partition("}")
        return ns, local
    return "", tag


# ─────────────────────────── Побудова відповіді ───────────────────────────


def prop_element(tag: str, inner: str = "") -> str:
    """<D:x> для DAV: або <x xmlns="ns"> для сторонніх просторів імен."""
    ns, local = split_tag(tag)
    if ns == DAV_NS:
        return f"<D:{local}>{inner}</D:{local}>" if inner else f"<D:{local}/>"
    xmlns = f" xmlns={quoteattr(ns)}" if ns else ' xmlns=""'
    return f"<{local}{xmlns}>{inner}</{local}>" if inner else f"<{local}{xmlns}/>"


STATUS_TEXT = {
    200: "OK",
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    424: "Failed Dependency",
}


def propstat(props: list[str], status: int) -> str:
    return (
        f"<D:propstat><D:prop>{''.join(props)}</D:prop>"
        f"<D:status>HTTP/1.1 {status} {STATUS_TEXT.get(status, '')}</D:status></D:propstat>"
    )


def response(href: str, propstats: list[str]) -> str:
    return f"<D:response><D:href>{escape(href)}</D:href>{''.join(propstats)}</D:response>"


def status_response(href: str, status: int, text: str = "") -> str:
    return f"<D:response><D:href>{escape(href)}</D:href><D:status>HTTP/1.1 {status} {text}</D:status></D:response>"


def multistatus(responses: list[str]) -> bytes:
    body = '<?xml version="1.0" encoding="utf-8"?>\n<D:multistatus xmlns:D="DAV:">' + "".join(responses) + "</D:multistatus>"
    return body.encode("utf-8")


def error_body(condition: str) -> bytes:
    return f'<?xml version="1.0" encoding="utf-8"?>\n<D:error xmlns:D="DAV:"><D:{condition}/></D:error>'.encode()


def text(value) -> str:
    return escape(str(value))
