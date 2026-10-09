// Редактор листа на TipTap (ProseMirror): власна модель документа замість застарілого document.execCommand.
// Бібліотека збирається Vite у наш бандл і віддається з нашого домену — CSP (script-src 'self') це дозволяє.
// injectCSS вимкнено: базові стилі ProseMirror — у styles.css (CSP забороняє вставлені <style>).
// Вставлений HTML розбирається за схемою редактора: усе, чого немає в схемі (скрипти, обробники, iframe), відкидається.

import { forwardRef, ReactNode, useCallback, useImperativeHandle, useRef, useState } from "react";
import { EditorContent, useEditor, useEditorState } from "@tiptap/react";
import type { Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Image } from "@tiptap/extension-image";
import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import { Placeholder } from "@tiptap/extensions";
import { useT } from "../i18n";
import type { TKey } from "../i18n";
import { cleanHtml, escapeHtml, normalizeLink } from "../lib/htmlClean";
import { Align, FontColor, FontFace, FontSize, QuoteBlock, SignatureBlock } from "./editorExtensions";
import EmojiPicker from "./EmojiPicker";
import Icon from "./Icon";

export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];

export interface RichEditorHandle {
  html: () => string;
  text: () => string;
  setHtml: (html: string) => void;
  focus: () => void;
  /** Замінює блок підпису (або додає його перед цитатою / в кінець). null — прибрати. */
  setSignature: (html: string | null) => void;
  /** Сумарний розмір вбудованих зображень (байти, приблизно). */
  imageBytes: () => number;
  isEmpty: () => boolean;
}

const SIZES: { value: string; label: TKey }[] = [
  { value: "2", label: "editor.size.small" },
  { value: "", label: "editor.size.normal" },
  { value: "5", label: "editor.size.large" },
  { value: "6", label: "editor.size.huge" },
];

// Шрифти, які є в усіх поширених поштових клієнтах
const FACES: { value: string; label: string }[] = [
  { value: "", label: "Sans Serif" },
  { value: "Georgia, 'Times New Roman', serif", label: "Serif" },
  { value: "'Courier New', Courier, monospace", label: "Fixed width" },
  { value: "Georgia, serif", label: "Georgia" },
  { value: "Garamond, Georgia, serif", label: "Garamond" },
  { value: "Tahoma, Geneva, sans-serif", label: "Tahoma" },
  { value: "'Trebuchet MS', Helvetica, sans-serif", label: "Trebuchet MS" },
  { value: "Verdana, Geneva, sans-serif", label: "Verdana" },
  { value: "'Comic Sans MS', 'Comic Sans', cursive", label: "Comic Sans MS" },
];

const COLORS = [
  "#000000", "#444444", "#666666", "#999999", "#cccccc", "#ffffff",
  "#c0392b", "#e67e22", "#f1c40f", "#27ae60", "#16a085", "#2980b9",
  "#8e44ad", "#d35400", "#7f8c8d", "#1abc9c", "#e84393", "#2c3e50",
];

const EMPTY_STATE = {
  bold: false,
  italic: false,
  underline: false,
  strike: false,
  bullet: false,
  ordered: false,
  quote: false,
  link: false,
  table: false,
  color: "",
  face: "",
  size: "",
  align: "",
  canUndo: false,
  canRedo: false,
};

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

const RichEditor = forwardRef<
  RichEditorHandle,
  {
    initialHtml?: string;
    placeholder?: string;
    onError?: (message: string) => void;
    onChange?: () => void;
    compact?: boolean;
    autoFocus?: boolean;
  }
>(function RichEditor({ initialHtml = "", placeholder, onError, onChange, compact, autoFocus }, ref) {
  const t = useT();
  const editorRef = useRef<Editor | null>(null);
  const onChangeRef = useRef(onChange);
  const onErrorRef = useRef(onError);
  onChangeRef.current = onChange;
  onErrorRef.current = onError;
  const [panel, setPanel] = useState<"" | "link" | "color" | "emoji">("");
  const [linkValue, setLinkValue] = useState("");

  const insertImages = useCallback(
    async (files: File[]) => {
      for (const f of files) {
        if (!IMAGE_TYPES.includes(f.type)) {
          onErrorRef.current?.(t("editor.imageType"));
          continue;
        }
        if (f.size > MAX_IMAGE_BYTES) {
          onErrorRef.current?.(t("editor.imageTooBig", { name: f.name }));
          continue;
        }
        const src = await readAsDataUrl(f);
        editorRef.current?.chain().focus().setImage({ src, alt: f.name }).run();
      }
    },
    [t],
  );

  const imageFiles = (list: FileList | null | undefined) =>
    Array.from((list || []) as FileList).filter((f) => f.type.startsWith("image/"));

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        code: false,
        codeBlock: false,
        heading: { levels: [1, 2, 3] },
        link: {
          openOnClick: false,
          autolink: true,
          linkOnPaste: true,
          defaultProtocol: "https",
          protocols: ["mailto"],
          isAllowedUri: (url: string) => normalizeLink(url) !== null,
          HTMLAttributes: { rel: "noopener noreferrer nofollow", target: "_blank" },
        },
      }),
      Image.configure({ allowBase64: true, inline: false }),
      Table.configure({ resizable: false, HTMLAttributes: { border: "1", cellpadding: "6", cellspacing: "0" } }),
      TableRow,
      TableHeader,
      TableCell,
      FontColor,
      FontFace,
      FontSize,
      Align,
      SignatureBlock,
      QuoteBlock,
      Placeholder.configure({ placeholder: placeholder || "" }),
    ],
    content: cleanHtml(initialHtml),
    injectCSS: false,
    shouldRerenderOnTransaction: false,
    autofocus: autoFocus ? "start" : false,
    editorProps: {
      attributes: {
        class: "rte-area",
        role: "textbox",
        "aria-multiline": "true",
        "aria-label": placeholder || t("editor.body"),
      },
      transformPastedHTML: (html: string) => cleanHtml(html),
      handlePaste: (_view, event) => {
        const files = imageFiles(event.clipboardData?.files);
        if (!files.length) return false;
        event.preventDefault();
        insertImages(files);
        return true;
      },
      handleDrop: (_view, event) => {
        const files = imageFiles(event.dataTransfer?.files);
        if (!files.length) return false;
        event.preventDefault();
        insertImages(files);
        return true;
      },
    },
    onUpdate: () => onChangeRef.current?.(),
  });
  editorRef.current = editor;

  const state =
    useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      bold: e?.isActive("bold") ?? false,
      italic: e?.isActive("italic") ?? false,
      underline: e?.isActive("underline") ?? false,
      strike: e?.isActive("strike") ?? false,
      bullet: e?.isActive("bulletList") ?? false,
      ordered: e?.isActive("orderedList") ?? false,
      quote: e?.isActive("blockquote") ?? false,
      link: e?.isActive("link") ?? false,
      table: e?.isActive("table") ?? false,
      color: String(e?.getAttributes("fontColor").color ?? ""),
      face: String(e?.getAttributes("fontFace").face ?? ""),
      size: String(e?.getAttributes("fontSize").size ?? ""),
      align: String(e?.getAttributes("paragraph").align ?? e?.getAttributes("heading").align ?? ""),
      canUndo: e?.can().undo() ?? false,
      canRedo: e?.can().redo() ?? false,
    }),
  }) ?? EMPTY_STATE;

  useImperativeHandle(ref, () => ({
    html: () => editorRef.current?.getHTML() || "",
    text: () => editorRef.current?.getText({ blockSeparator: "\n" }) || "",
    setHtml: (html) => {
      editorRef.current?.commands.setContent(cleanHtml(html));
    },
    focus: () => {
      editorRef.current?.commands.focus();
    },
    imageBytes: () => {
      let total = 0;
      editorRef.current?.state.doc.descendants((node) => {
        const src = node.type.name === "image" ? String(node.attrs.src || "") : "";
        if (src.startsWith("data:")) total += Math.floor((src.length - src.indexOf(",") - 1) * 0.75);
        return true;
      });
      return total;
    },
    isEmpty: () => {
      const ed = editorRef.current;
      if (!ed) return true;
      let hasImage = false;
      ed.state.doc.descendants((node) => {
        if (node.type.name === "image") hasImage = true;
        return !hasImage;
      });
      return !ed.getText().trim() && !hasImage;
    },
    setSignature: (html) => {
      const ed = editorRef.current;
      if (!ed) return;
      const found: { range?: { from: number; to: number }; quote?: number } = {};
      ed.state.doc.forEach((node, offset) => {
        if (node.type.name === "signatureBlock" && !found.range) found.range = { from: offset, to: offset + node.nodeSize };
        if (node.type.name === "quoteBlock" && found.quote === undefined) found.quote = offset;
      });
      const block = html ? `<div class="bc-signature"><p>--&nbsp;</p>${cleanHtml(html)}</div>` : null;
      if (found.range) {
        if (block) ed.chain().insertContentAt(found.range, block).run();
        else ed.chain().deleteRange(found.range).run();
      } else if (block) {
        ed.chain().insertContentAt(found.quote ?? ed.state.doc.content.size, block).run();
      }
    },
  }));

  if (!editor) return null;
  const chain = () => editor.chain().focus();

  const applyLink = () => {
    const href = normalizeLink(linkValue);
    if (!href) {
      onErrorRef.current?.(t("editor.badLink"));
      return;
    }
    if (editor.state.selection.empty && !editor.isActive("link")) {
      chain().insertContent(`<a href="${escapeHtml(href)}">${escapeHtml(linkValue.trim())}</a>`).run();
    } else {
      chain().extendMarkRange("link").setLink({ href }).run();
    }
    setPanel("");
    setLinkValue("");
  };

  const btn = (label: TKey, active: boolean, run: () => void, content: ReactNode, disabled = false) => (
    <button
      type="button"
      className={`rte-btn ${active ? "active" : ""}`}
      title={t(label)}
      aria-label={t(label)}
      aria-pressed={active}
      disabled={disabled}
      onMouseDown={(e) => e.preventDefault()}
      onClick={run}
    >
      {content}
    </button>
  );

  return (
    <div className={`rte ${compact ? "rte-compact" : ""}`}>
      <div className="rte-toolbar" role="toolbar" aria-label={t("editor.toolbar")}>
        {btn("editor.undo", false, () => chain().undo().run(), <Icon name="undo" size={16} />, !state.canUndo)}
        {btn("editor.redo", false, () => chain().redo().run(), <Icon name="redo" size={16} />, !state.canRedo)}
        <span className="rte-sep" />
        <select
          className="select-sm rte-select"
          aria-label={t("editor.font")}
          title={t("editor.font")}
          value={state.face}
          onChange={(e) =>
            e.target.value ? chain().setMark("fontFace", { face: e.target.value }).run() : chain().unsetMark("fontFace").run()
          }
        >
          {FACES.map((f) => (
            <option key={f.label} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
        <select
          className="select-sm rte-select"
          aria-label={t("editor.fontSize")}
          title={t("editor.fontSize")}
          value={state.size}
          onChange={(e) =>
            e.target.value ? chain().setMark("fontSize", { size: e.target.value }).run() : chain().unsetMark("fontSize").run()
          }
        >
          {SIZES.map((s) => (
            <option key={s.label} value={s.value}>
              {t(s.label)}
            </option>
          ))}
        </select>
        <span className="rte-sep" />
        {btn("editor.bold", state.bold, () => chain().toggleBold().run(), <b>B</b>)}
        {btn("editor.italic", state.italic, () => chain().toggleItalic().run(), <i>I</i>)}
        {btn("editor.underline", state.underline, () => chain().toggleUnderline().run(), <u>U</u>)}
        {btn("editor.strike", state.strike, () => chain().toggleStrike().run(), <s>S</s>)}
        {btn(
          "editor.color",
          panel === "color",
          () => setPanel(panel === "color" ? "" : "color"),
          <span className="rte-color-btn">
            A<span className="rte-color-bar" style={{ background: state.color || "currentColor" }} />
          </span>,
        )}
        <span className="rte-sep" />
        {btn("editor.alignLeft", !state.align || state.align === "left", () => chain().setAlign(null).run(), <Icon name="alignLeft" size={16} />)}
        {btn("editor.alignCenter", state.align === "center", () => chain().setAlign("center").run(), <Icon name="alignCenter" size={16} />)}
        {btn("editor.alignRight", state.align === "right", () => chain().setAlign("right").run(), <Icon name="alignRight" size={16} />)}
        <span className="rte-sep" />
        {btn("editor.bullets", state.bullet, () => chain().toggleBulletList().run(), <Icon name="list" size={16} />)}
        {btn("editor.numbers", state.ordered, () => chain().toggleOrderedList().run(), <Icon name="listOl" size={16} />)}
        {btn("editor.quote", state.quote, () => chain().toggleBlockquote().run(), <Icon name="quote" size={16} />)}
        <span className="rte-sep" />
        {btn(
          "editor.link",
          state.link || panel === "link",
          () => {
            const { from, to } = editor.state.selection;
            setLinkValue(String(editor.getAttributes("link").href || editor.state.doc.textBetween(from, to, " ").trim()));
            setPanel(panel === "link" ? "" : "link");
          },
          <Icon name="link" size={16} />,
        )}
        {state.link && btn("editor.unlink", false, () => chain().extendMarkRange("link").unsetLink().run(), <Icon name="x" size={14} />)}
        <label className="rte-btn" title={t("editor.image")} aria-label={t("editor.image")}>
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
        {btn(
          "editor.table",
          state.table,
          () => chain().insertTable({ rows: 3, cols: 3, withHeaderRow: false }).run(),
          <Icon name="table" size={16} />,
          state.table,
        )}
        <span className="rte-emoji-anchor">
          {btn("editor.emoji", panel === "emoji", () => setPanel(panel === "emoji" ? "" : "emoji"), <Icon name="smile" size={16} />)}
          {panel === "emoji" && (
            <EmojiPicker className="rte-emoji" onClose={() => setPanel("")} onPick={(emoji) => chain().insertContent(emoji).run()} />
          )}
        </span>
        {btn("editor.clear", false, () => chain().unsetAllMarks().setAlign(null).run(), <Icon name="eraser" size={16} />)}
      </div>

      {panel === "link" && (
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
              if (e.key === "Escape") setPanel("");
            }}
          />
          <button type="button" className="btn btn-sm btn-primary" onClick={applyLink}>
            {t("common.ok")}
          </button>
          <button type="button" className="btn btn-sm" onClick={() => setPanel("")}>
            {t("common.cancel")}
          </button>
        </div>
      )}

      {panel === "color" && (
        <div className="rte-colorbar" role="radiogroup" aria-label={t("editor.color")}>
          {COLORS.map((c) => (
            <button
              key={c}
              type="button"
              role="radio"
              aria-checked={state.color === c}
              aria-label={c}
              title={c}
              className={`rte-swatch ${state.color === c ? "active" : ""}`}
              style={{ background: c }}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                chain().setMark("fontColor", { color: c }).run();
                setPanel("");
              }}
            />
          ))}
          <button
            type="button"
            className="btn btn-sm"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              chain().unsetMark("fontColor").run();
              setPanel("");
            }}
          >
            {t("editor.colorReset")}
          </button>
        </div>
      )}

      {state.table && (
        <div className="rte-tablebar" role="toolbar" aria-label={t("editor.table")}>
          {btn("editor.rowAbove", false, () => chain().addRowBefore().run(), t("editor.rowAbove"))}
          {btn("editor.rowBelow", false, () => chain().addRowAfter().run(), t("editor.rowBelow"))}
          {btn("editor.colLeft", false, () => chain().addColumnBefore().run(), t("editor.colLeft"))}
          {btn("editor.colRight", false, () => chain().addColumnAfter().run(), t("editor.colRight"))}
          {btn("editor.deleteRow", false, () => chain().deleteRow().run(), t("editor.deleteRow"))}
          {btn("editor.deleteCol", false, () => chain().deleteColumn().run(), t("editor.deleteCol"))}
          {btn("editor.deleteTable", false, () => chain().deleteTable().run(), t("editor.deleteTable"))}
        </div>
      )}

      <EditorContent editor={editor} />
    </div>
  );
});

export default RichEditor;
