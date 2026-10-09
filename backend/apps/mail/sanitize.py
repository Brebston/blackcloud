"""Санітизація HTML листів (nh3 / ammonia, Rust).

* видаляються скрипти, обробники подій, форми, iframe, object тощо;
* віддалені зображення блокуються за замовчуванням (трекінг-пікселі);
* cid:-зображення підставляються як data: URI;
* усі посилання відкриваються в новій вкладці з rel=noopener noreferrer.
Додатково HTML показується у sandbox-iframe з жорстким CSP.
"""

import re

import nh3

ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "center", "code", "col", "colgroup", "dd", "del", "div",
    "dl", "dt", "em", "font", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "li", "ol", "p",
    "pre", "q", "s", "small", "span", "strike", "strong", "sub", "sup", "table", "tbody", "td", "tfoot", "th",
    "thead", "tr", "tt", "u", "ul",
}
COMMON_ATTRS = {"style", "align", "valign", "width", "height", "bgcolor", "color", "dir", "title", "class"}
ALLOWED_ATTRS = {
    "*": COMMON_ATTRS,
    "a": {"href", "name"},
    "img": {"src", "alt", "border"},
    "font": {"face", "size", "color"},
    "table": {"border", "cellpadding", "cellspacing"},
    "td": {"colspan", "rowspan", "nowrap"},
    "th": {"colspan", "rowspan", "nowrap"},
}
BLOCKED_IMG = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
# url(...) у CSS — шлях для трекінгу і витоку даних
CSS_URL_RE = re.compile(r"url\s*\([^)]*\)", re.IGNORECASE)
CSS_DANGEROUS_RE = re.compile(r"(expression\s*\(|javascript:|behavior\s*:|-moz-binding)", re.IGNORECASE)


def sanitize_html(html: str, *, inline_images: dict[str, str], allow_remote: bool) -> str:
    def attribute_filter(tag, attr, value):
        if attr == "style":
            value = CSS_URL_RE.sub("none", value)
            if CSS_DANGEROUS_RE.search(value):
                return None
            return value
        if tag == "img" and attr == "src":
            v = value.strip()
            if v.lower().startswith("cid:"):
                return inline_images.get(v[4:].strip("<>"), BLOCKED_IMG)
            if v.lower().startswith("data:image/") and not v.lower().startswith("data:image/svg"):
                return v
            if allow_remote and v.lower().startswith("https://"):
                return v
            return BLOCKED_IMG
        if tag == "a" and attr == "href":
            v = value.strip().lower()
            if not v.startswith(("https://", "http://", "mailto:", "#")):
                return None
        return value

    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        url_schemes={"http", "https", "mailto", "data", "cid"},
        link_rel="noopener noreferrer nofollow",
        set_tag_attribute_values={"a": {"target": "_blank"}},
        attribute_filter=attribute_filter,
        strip_comments=True,
    )


# ─────────────────────── Вихідні листи (редактор веб-пошти) ───────────────────────

OUTGOING_IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
DATA_IMAGE_RE = re.compile(r"^data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=\s]+)$", re.IGNORECASE)


def sanitize_outgoing_html(html: str) -> str:
    """HTML з редактора листа. Той самий білий список тегів, що й для вхідних листів;
    зображення — лише вбудовані data:image (растрові) або https://."""

    def attribute_filter(tag, attr, value):
        if attr == "style":
            value = CSS_URL_RE.sub("none", value)
            return None if CSS_DANGEROUS_RE.search(value) else value
        if tag == "img" and attr == "src":
            v = value.strip()
            if DATA_IMAGE_RE.match(v) or v.lower().startswith("https://"):
                return v
            return None
        if tag == "a" and attr == "href":
            v = value.strip().lower()
            if not v.startswith(("https://", "http://", "mailto:")):
                return None
        return value

    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        url_schemes={"http", "https", "mailto", "data"},
        link_rel="noopener noreferrer nofollow",
        attribute_filter=attribute_filter,
        strip_comments=True,
    )


def extract_data_images(html: str, domain: str, max_images: int = 30) -> tuple[str, list[dict]]:
    """Замінює вбудовані data:image на cid:-посилання (як у Gmail: multipart/related).

    Повертає (html з cid:, [{cid, maintype, subtype, data}])."""
    import base64
    import binascii
    import secrets as _secrets

    images: list[dict] = []
    src_re = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', re.IGNORECASE)

    def repl(match):
        src = match.group(2)
        m = DATA_IMAGE_RE.match(src.replace("&#43;", "+"))
        if not m:
            return match.group(0)
        if len(images) >= max_images:
            raise ValueError("too many images")
        try:
            data = base64.b64decode(re.sub(r"\s", "", m.group(2)), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("bad image") from exc
        cid = f"img{len(images)}.{_secrets.token_hex(8)}@{domain}"
        maintype, subtype = m.group(1).lower().split("/")
        images.append({"cid": cid, "maintype": maintype, "subtype": subtype, "data": data})
        return f"{match.group(1)}cid:{cid}{match.group(3)}"

    return src_re.sub(repl, html), images


def html_to_text(html: str) -> str:
    """Текстова версія HTML-листа (для клієнтів без HTML і фільтрів спаму)."""
    from html.parser import HTMLParser

    blocks = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "table"}

    class Collector(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.out: list[str] = []
            self.href: str | None = None
            self.skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("style", "script"):
                self.skip += 1
            if tag in blocks:
                self.out.append("\n")
            if tag == "li":
                self.out.append("• ")
            if tag == "a":
                self.href = dict(attrs).get("href")
            if tag == "img":
                alt = dict(attrs).get("alt")
                if alt:
                    self.out.append(f"[{alt}]")

        def handle_endtag(self, tag):
            if tag in ("style", "script") and self.skip:
                self.skip -= 1
            if tag == "a" and self.href:
                if self.href.startswith(("http", "mailto:")):
                    self.out.append(f" ({self.href.removeprefix('mailto:')})")
                self.href = None
            if tag in blocks:
                self.out.append("\n")

        def handle_data(self, data):
            if not self.skip:
                self.out.append(data)

    parser = Collector()
    parser.feed(html)
    parser.close()
    text = "".join(parser.out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
