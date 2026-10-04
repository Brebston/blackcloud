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
