import { useEffect, useState } from "react";
import { get } from "../api/client";
import type { FileItem } from "../api/types";
import { tr } from "../i18n";

export type ViewKind = "image" | "pdf" | "video" | "audio" | "text" | "office";

// Те, що браузер показує нативно (решта зображень, напр. HEIC, — через мініатюру)
const BROWSER_IMAGES = ["image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"];
const OFFICE_EXT = ["docx", "doc", "docm", "dotx", "odt", "rtf", "xlsx", "xls", "xlsm", "ods", "csv", "pptx", "ppt", "ppsx", "odp"];

export function extension(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}

export function viewKind(f: FileItem, officeEnabled: boolean): ViewKind | null {
  if (!f.downloadable) return null;
  if (BROWSER_IMAGES.includes(f.mime_type) || (f.mime_type.startsWith("image/") && f.has_thumbnail)) return "image";
  if (f.mime_type === "application/pdf") return "pdf";
  if (f.mime_type === "video/mp4" || f.mime_type === "video/webm") return "video";
  if (["audio/mpeg", "audio/ogg", "audio/wav"].includes(f.mime_type)) return "audio";
  if (officeEnabled && OFFICE_EXT.includes(extension(f.name))) return "office";
  if (f.mime_type === "text/plain") return "text";
  return null;
}

/** Чи потрібен окремий origin (usercontent) для показу: усе, крім мініатюр. */
export function needsPreviewUrl(f: FileItem, kind: ViewKind | null): boolean {
  if (kind === "image") return BROWSER_IMAGES.includes(f.mime_type);
  return kind === "pdf" || kind === "video" || kind === "audio" || kind === "text";
}

/**
 * Адреса для перегляду в браузері на піддомені usercontent.<домен>.
 * Вміст користувачів (PDF, текст, відео) ніколи не відкривається в origin основного сайту:
 * навіть шкідливий файл не зможе прочитати cookie чи викликати API.
 */
export function usePreviewUrl(f: FileItem | undefined, kind: ViewKind | null) {
  const [state, setState] = useState<{ id: string; url: string; error: string } | null>(null);
  const needed = !!f && needsPreviewUrl(f, kind);
  const id = f?.id;
  const version = f?.content_version;
  useEffect(() => {
    if (!needed || !id) return;
    let cancelled = false;
    setState(null);
    get<{ url: string }>(`/api/files/items/${id}/preview/`)
      .then((r) => !cancelled && setState({ id, url: r.url, error: "" }))
      .catch(() => !cancelled && setState({ id, url: "", error: tr("files.viewer.previewFailed") }));
    return () => {
      cancelled = true;
    };
  }, [needed, id, version]);
  if (!needed || !f) return { url: "", loading: false, error: "" };
  if (!state || state.id !== f.id) return { url: "", loading: true, error: "" };
  return { url: state.url, loading: false, error: state.error };
}

export function imageSrc(f: FileItem, previewUrl: string): string {
  return BROWSER_IMAGES.includes(f.mime_type) ? previewUrl : thumbnailUrl(f);
}

export const downloadUrl = (f: FileItem) => `/api/files/items/${f.id}/download/`;
export const thumbnailUrl = (f: FileItem) => `/api/files/items/${f.id}/thumbnail/?v=${f.content_version}`;
