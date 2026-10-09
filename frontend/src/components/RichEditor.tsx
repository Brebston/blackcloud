// Простий WYSIWYG-редактор листа (contentEditable + document.execCommand).
// execCommand офіційно застарілий, але досі підтримується всіма браузерами і не потребує
// сторонніх бібліотек (CSP проєкту забороняє зовнішні скрипти й inline-стилі).

import { forwardRef, ReactNode, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { useT } from "../i18n";
import type { TKey } from "../i18n";
import { cleanHtml, escapeHtml, normalizeLink } from "../lib/htmlClean";
import EmojiPicker from "./EmojiPicker";
import Icon from "./Icon";

export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];

export interface RichEditorHandle {
  html: () => string;
  text: () => string;
  setHtml: (html: string) => void;
  focus: () => void;
  /** Замінює блок підпису (або додає його перед цитатою / в кінець). */
  setSignature: (html: string | null) => void;
  /** Сумарний розмір вбудованих зображень (байти, приблизно). */
  imageBytes: () => number;
  isEmpty: () => boolean;
}

const SIZES: { value: string; label: TKey }[] = [
  { value: "2", label: "editor.size.small" },
  { value: "3", label: "editor.size.normal" },
  { value: "5", label: "editor.size.large" },
  { value: "6", label: "editor.size.huge" },
];

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

const RichEditor = forwardRef<RichEditorHandle, {
  initialHtml?: string;
  placeholder?: string;
  onError?: (message: string) => void;
  compact?: boolean;
  autoFocus?: boolean;
}>(function RichEditor({ initialHtml = "", placeholder, onError, compact, autoFocus }, ref) {
  const t = useT();
  const area = useRef<HTMLDivElement>(null);
  const saved = useRef<Range | null>(null);
  const [emoji, setEmoji] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [linkValue, setLinkValue] = useState("");
  const [, force] = useState(0);

  useEffect(() => {
    if (area.current) area.current.innerHTML = cleanHtml(initialHtml);
    // Атрибути <font size> замість inline-стилів: CSP сторінки не дозволяє style-атрибути
    try {
      document.execCommand("styleWithCSS", false, "false");
    } catch {
      /* ignore */
    }
    if (autoFocus) area.current?.focus();
    // initialHtml використовується лише при монтуванні
  }, []);

  const saveSelection = () => {
    const sel = window.getSelection();
    if (sel && sel.rangeCount && area.current?.contains(sel.anchorNode)) saved.current = sel.getRangeAt(0).cloneRange();
  };

  const restoreSelection = () => {
    area.current?.focus();
    const sel = window.getSelection();
    if (saved.current && sel) {
      sel.removeAllRanges();
      sel.addRange(saved.current);
    }
  };

  const exec = (command: string, value?: string) => {
    restoreSelection();
    document.execCommand(command, false, value);
    saveSelection();
    force((n) => n + 1);
  };

  const insertHtml = (html: string) => exec("insertHTML", html);

  const insertImages = useCallback(
    async (files: File[]) => {
      for (const f of files) {
        if (!IMAGE_TYPES.includes(f.type)) {
          onError?.(t("editor.imageType"));
          continue;
        }
        if (f.size > MAX_IMAGE_BYTES) {
          onError?.(t("editor.imageTooBig", { name: f.name }));
          continue;
        }
        const url = await readAsDataUrl(f);
        insertHtml(`<img src="${url}" alt="${escapeHtml(f.name)}">`);
      }
    },
    [onError, t],
  );

  useImperativeHandle(ref, () => ({
    html: () => area.current?.innerHTML || "",
    text: () => area.current?.innerText || "",
    setHtml: (html) => {
      if (area.current) area.current.innerHTML = cleanHtml(html);
    },
    focus: () => area.current?.focus(),
    imageBytes: () =>
      Array.from(area.current?.querySelectorAll("img") || []).reduce((sum, img) => {
        const src = img.getAttribute("src") || "";
        return sum + (src.startsWith("data:") ? Math.floor((src.length - src.indexOf(",") - 1) * 0.75) : 0);
      }, 0),
    isEmpty: () => !(area.current?.innerText.trim() || area.current?.querySelector("img")),
    setSignature: (html) => {
      const root = area.current;
      if (!root) return;
      const existing = root.querySelector(".bc-signature");
      if (!html) {
        existing?.remove();
        return;
      }
      const block = document.createElement("div");
      block.className = "bc-signature";
      block.innerHTML = `<div>--&nbsp;</div>${cleanHtml(html)}`;
      if (existing) existing.replaceWith(block);
      else {
        const quote = root.querySelector(".bc-quote");
        if (quote) root.insertBefore(block, quote);
        else root.appendChild(block);
      }
    },
  }));

  const applyLink = () => {
    const href = normalizeLink(linkValue);
    if (!href) {
      onError?.(t("editor.badLink"));
      return;
    }
    restoreSelection();
    const sel = window.getSelection();
    if (sel && sel.isCollapsed) insertHtml(`<a href="${escapeHtml(href)}">${escapeHtml(linkValue.trim())}</a>`);
    else exec("createLink", href);
    setLinkOpen(false);
    setLinkValue("");
  };

  // Кнопки не забирають фокус у редактора, тож виділення не втрачається
  const btn = (label: TKey, command: string, content: ReactNode, value?: string) => (
    <button
      type="button"
      className="rte-btn"
      title={t(label)}
      aria-label={t(label)}
      onMouseDown={(e) => e.preventDefault()}
      onClick={() => exec(command, value)}
    >
      {content}
    </button>
  );

  return (
    <div className={`rte ${compact ? "rte-compact" : ""}`}>
      <div className="rte-toolbar" role="toolbar" aria-label={t("editor.toolbar")}>
        <select
          className="select-sm"
          aria-label={t("editor.fontSize")}
          title={t("editor.fontSize")}
          value=""
          onMouseDown={saveSelection}
          onChange={(e) => e.target.value && exec("fontSize", e.target.value)}
        >
          <option value="">{t("editor.fontSize")}</option>
          {SIZES.map((s) => (
            <option key={s.value} value={s.value}>
              {t(s.label)}
            </option>
          ))}
        </select>
        <span className="rte-sep" />
        {btn("editor.bold", "bold", <b>B</b>)}
        {btn("editor.italic", "italic", <i>I</i>)}
        {btn("editor.underline", "underline", <u>U</u>)}
        {btn("editor.strike", "strikeThrough", <s>S</s>)}
        <span className="rte-sep" />
        {btn("editor.bullets", "insertUnorderedList", <Icon name="list" size={16} />)}
        {btn("editor.numbers", "insertOrderedList", <Icon name="listOl" size={16} />)}
        {btn("editor.quote", "formatBlock", <Icon name="quote" size={16} />, "blockquote")}
        <span className="rte-sep" />
        <button
          type="button"
          className={`rte-btn ${linkOpen ? "active" : ""}`}
          title={t("editor.link")}
          aria-label={t("editor.link")}
          onMouseDown={(e) => {
            e.preventDefault();
            saveSelection();
          }}
          onClick={() => {
            setLinkValue(window.getSelection()?.toString().trim() || "");
            setLinkOpen(!linkOpen);
          }}
        >
          <Icon name="link" size={16} />
        </button>
        {btn("editor.unlink", "unlink", <Icon name="x" size={14} />)}
        <label className="rte-btn" title={t("editor.image")} aria-label={t("editor.image")} onMouseDown={saveSelection}>
          <Icon name="image" size={16} />
          <input
            type="file"
            accept={IMAGE_TYPES.join(",")}
            multiple
            hidden
            onChange={(e) => {
              const files = Array.from((e.target.files || []) as FileList);
              e.target.value = "";
              insertImages(files);
            }}
          />
        </label>
        <span className="rte-emoji-anchor">
          <button
            type="button"
            className={`rte-btn ${emoji ? "active" : ""}`}
            title={t("editor.emoji")}
            aria-label={t("editor.emoji")}
            onMouseDown={(e) => {
              e.preventDefault();
              saveSelection();
            }}
            onClick={() => setEmoji(!emoji)}
          >
            <Icon name="smile" size={16} />
          </button>
          {emoji && (
            <EmojiPicker
              className="rte-emoji"
              onClose={() => setEmoji(false)}
              onPick={(e) => {
                exec("insertText", e);
              }}
            />
          )}
        </span>
        {btn("editor.clear", "removeFormat", <Icon name="eraser" size={16} />)}
      </div>
      {linkOpen && (
        <div className="rte-linkbar">
          <input
            autoFocus
            placeholder={t("editor.linkPlaceholder")}
            value={linkValue}
            onChange={(e) => setLinkValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                applyLink();
              }
              if (e.key === "Escape") setLinkOpen(false);
            }}
          />
          <button type="button" className="btn btn-sm btn-primary" onClick={applyLink}>
            {t("common.ok")}
          </button>
          <button type="button" className="btn btn-sm" onClick={() => setLinkOpen(false)}>
            {t("common.cancel")}
          </button>
        </div>
      )}
      <div
        ref={area}
        className="rte-area"
        contentEditable
        role="textbox"
        aria-multiline="true"
        aria-label={placeholder || t("editor.body")}
        data-placeholder={placeholder || ""}
        onKeyUp={saveSelection}
        onMouseUp={saveSelection}
        onBlur={saveSelection}
        onPaste={(e) => {
          const files = Array.from((e.clipboardData.files || []) as FileList).filter((f) => f.type.startsWith("image/"));
          if (files.length) {
            e.preventDefault();
            insertImages(files);
            return;
          }
          const html = e.clipboardData.getData("text/html");
          if (html) {
            e.preventDefault();
            document.execCommand("insertHTML", false, cleanHtml(html));
          }
        }}
        onDrop={(e) => {
          const files = Array.from((e.dataTransfer.files || []) as FileList).filter((f) => f.type.startsWith("image/"));
          if (files.length) {
            e.preventDefault();
            area.current?.focus();
            insertImages(files);
          }
        }}
      />
    </div>
  );
});

export default RichEditor;
