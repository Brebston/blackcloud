// Розширення TipTap для редактора листів.
//
// Колір, шрифт і розмір тексту зберігаються як <font color|face|size>, а вирівнювання — як атрибут align.
// Причини: 1) CSP сайту не дозволяє inline-стилі (style="…" у редакторі не застосовувався б);
// 2) поштові клієнти (Outlook, Gmail) надійно розуміють саме ці атрибути;
// 3) серверний санітизатор (nh3) їх пропускає.

import { Extension, Mark, Node } from "@tiptap/core";

/** rgb()/rgba()/#abc/#aabbcc → #aabbcc; усе інше відкидається. */
export function toHexColor(value: string | null | undefined): string | null {
  const v = (value || "").trim().toLowerCase();
  let m = v.match(/^#([0-9a-f]{3})$/);
  if (m) return `#${m[1].split("").map((c) => c + c).join("")}`;
  m = v.match(/^#([0-9a-f]{6})$/);
  if (m) return v;
  m = v.match(/^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,\s*([\d.]+))?\s*\)$/);
  if (m) {
    if (m[4] !== undefined && Number(m[4]) === 0) return null;
    return `#${[m[1], m[2], m[3]].map((n) => Math.min(255, Number(n)).toString(16).padStart(2, "0")).join("")}`;
  }
  return null;
}

const FACE_RE = /^[\w\s,'"-]{1,100}$/;
const cleanFace = (v: string | null | undefined) => {
  const face = (v || "").trim();
  return face && FACE_RE.test(face) ? face : null;
};
const cleanSize = (v: string | null | undefined) => (/^[1-7]$/.test((v || "").trim()) ? (v || "").trim() : null);

export const FontColor = Mark.create({
  name: "fontColor",
  addAttributes() {
    return {
      color: {
        default: null,
        parseHTML: (el: HTMLElement) => toHexColor(el.getAttribute("color")),
        renderHTML: (attrs: { color?: string | null }) => (attrs.color ? { color: attrs.color } : {}),
      },
    };
  },
  parseHTML() {
    return [
      { tag: "font[color]", consuming: false },
      {
        style: "color",
        consuming: false,
        getAttrs: (value: string) => {
          const color = toHexColor(value);
          return color ? { color } : false;
        },
      },
    ];
  },
  renderHTML({ HTMLAttributes }) {
    return ["font", HTMLAttributes, 0];
  },
});

export const FontFace = Mark.create({
  name: "fontFace",
  addAttributes() {
    return {
      face: {
        default: null,
        parseHTML: (el: HTMLElement) => cleanFace(el.getAttribute("face")),
        renderHTML: (attrs: { face?: string | null }) => (attrs.face ? { face: attrs.face } : {}),
      },
    };
  },
  parseHTML() {
    return [{ tag: "font[face]", consuming: false }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["font", HTMLAttributes, 0];
  },
});

export const FontSize = Mark.create({
  name: "fontSize",
  addAttributes() {
    return {
      size: {
        default: null,
        parseHTML: (el: HTMLElement) => cleanSize(el.getAttribute("size")),
        renderHTML: (attrs: { size?: string | null }) => (attrs.size ? { size: attrs.size } : {}),
      },
    };
  },
  parseHTML() {
    return [{ tag: "font[size]", consuming: false }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["font", HTMLAttributes, 0];
  },
});

const ALIGNS = ["left", "center", "right", "justify"];

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    align: {
      /** null — вирівнювання за замовчуванням (ліворуч). */
      setAlign: (align: string | null) => ReturnType;
    };
  }
}

/** Вирівнювання абзаців і заголовків через атрибут align (без inline-стилів). */
export const Align = Extension.create({
  name: "align",
  addGlobalAttributes() {
    return [
      {
        types: ["paragraph", "heading"],
        attributes: {
          align: {
            default: null,
            parseHTML: (el: HTMLElement) => {
              const v = (el.getAttribute("align") || el.style.textAlign || "").toLowerCase();
              return ALIGNS.includes(v) ? v : null;
            },
            renderHTML: (attrs: { align?: string | null }) => (attrs.align && attrs.align !== "left" ? { align: attrs.align } : {}),
          },
        },
      },
    ];
  },
  addCommands() {
    return {
      setAlign:
        (align: string | null) =>
        ({ commands }) => {
          const value = align && ALIGNS.includes(align) && align !== "left" ? align : null;
          const p = commands.updateAttributes("paragraph", { align: value });
          const h = commands.updateAttributes("heading", { align: value });
          return p || h;
        },
    };
  },
});

/** Блок підпису (<div class="bc-signature">): його можна замінити іншим підписом або прибрати. */
export const SignatureBlock = Node.create({
  name: "signatureBlock",
  group: "block",
  content: "block+",
  defining: true,
  parseHTML() {
    return [{ tag: "div.bc-signature" }];
  },
  renderHTML() {
    return ["div", { class: "bc-signature" }, 0];
  },
});

/** Цитата листа, на який відповідаємо, або переслане повідомлення (<div class="bc-quote">). */
export const QuoteBlock = Node.create({
  name: "quoteBlock",
  group: "block",
  content: "block+",
  defining: true,
  parseHTML() {
    return [{ tag: "div.bc-quote" }];
  },
  renderHTML() {
    return ["div", { class: "bc-quote" }, 0];
  },
});
