// Клієнтське очищення HTML для редактора листів (вставка з буфера, підписи).
// Це лише зручність і перша лінія: остаточну санітизацію робить сервер (nh3),
// а сторінку додатково захищає CSP без 'unsafe-inline'.

const TAGS = new Set([
  "a", "b", "blockquote", "br", "code", "div", "em", "font", "h1", "h2", "h3", "hr", "i", "img", "li", "ol",
  "p", "pre", "s", "small", "span", "strike", "strong", "sub", "sup", "table", "tbody", "td", "th", "thead",
  "tr", "u", "ul",
]);
const ATTRS: Record<string, string[]> = {
  "*": ["align", "dir", "title", "class", "style"],
  a: ["href"],
  img: ["src", "alt", "width", "height"],
  font: ["size", "color", "face"],
  td: ["colspan", "rowspan"],
  th: ["colspan", "rowspan"],
};
// Елементи, які видаляються разом із вмістом
const DROP = new Set(["script", "style", "iframe", "object", "embed", "form", "input", "button", "textarea", "select", "svg", "math", "template", "noscript", "link", "meta", "title", "head"]);

export const DATA_IMAGE = /^data:image\/(png|jpeg|gif|webp);base64,[a-z0-9+/=\s]+$/i;

/** Безпечне посилання: http(s) або mailto. Голий домен → https://, адреса → mailto:. */
export function normalizeLink(raw: string): string | null {
  const v = raw.trim();
  if (!v || /\s/.test(v)) return null;
  if (/^(https?:\/\/|mailto:)/i.test(v)) {
    try {
      const u = new URL(v);
      return ["http:", "https:", "mailto:"].includes(u.protocol) ? u.href : null;
    } catch {
      return null;
    }
  }
  if (/^[^@\s/]+@[^@\s/]+\.[^@\s/]+$/.test(v)) return `mailto:${v}`;
  if (/^[a-z0-9-]+(\.[a-z0-9-]+)+([/?#].*)?$/i.test(v)) return normalizeLink(`https://${v}`);
  return null;
}

function cleanNode(node: Node, doc: Document) {
  for (const child of Array.from(node.childNodes)) {
    if (child.nodeType === Node.COMMENT_NODE) {
      child.remove();
      continue;
    }
    if (child.nodeType !== Node.ELEMENT_NODE) continue;
    const el = child as Element;
    const tag = el.tagName.toLowerCase();
    if (DROP.has(tag)) {
      el.remove();
      continue;
    }
    cleanNode(el, doc);
    if (!TAGS.has(tag)) {
      // Невідомий тег: залишаємо лише вміст
      el.replaceWith(...Array.from(el.childNodes));
      continue;
    }
    const allowed = new Set([...(ATTRS["*"] || []), ...(ATTRS[tag] || [])]);
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      if (!allowed.has(name)) {
        el.removeAttribute(attr.name);
        continue;
      }
      if (name === "style" && /(url\s*\(|expression\s*\(|javascript:|behavior\s*:|-moz-binding)/i.test(attr.value)) {
        el.removeAttribute(attr.name);
      }
    }
    if (tag === "a") {
      const href = normalizeLink(el.getAttribute("href") || "");
      if (href) el.setAttribute("href", href);
      else el.removeAttribute("href");
    }
    if (tag === "img") {
      const src = (el.getAttribute("src") || "").trim();
      if (!DATA_IMAGE.test(src) && !/^https:\/\//i.test(src)) el.remove();
    }
  }
}

/** Повертає очищений HTML. DOMParser створює «інертний» документ: скрипти не виконуються, ресурси не вантажаться. */
export function cleanHtml(html: string): string {
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  cleanNode(doc.body, doc);
  return doc.body.innerHTML;
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
}

/** Текст → HTML із збереженням рядків. */
export function textToHtml(text: string): string {
  return escapeHtml(text).replace(/\r?\n/g, "<br>");
}
